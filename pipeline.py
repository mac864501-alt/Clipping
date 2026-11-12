#!/usr/bin/env python3
"""Twitch/Kick VOD -> AI-picked clips -> 9:16 over gameplay + captions -> YouTube Shorts + Instagram Reels.
Runs on GitHub Actions. Config is at the top; secrets come from environment variables."""
import json
import os
import random
import re
import shutil
import subprocess
import time
from pathlib import Path

import requests

# ---------------- CONFIG ----------------
MAX_VODS_PER_RUN = 1          # VODs processed per workflow run
MAX_CLIPS_PER_VOD = 4         # clips made per VOD
CLIP_MIN, CLIP_MAX = 25, 59   # clip length in seconds
WHISPER_MODEL = "base"        # "small" = better accuracy, ~3x slower
WAIT_AFTER_STREAM_MIN = 30    # skip VODs of streams that are live or ended < this many minutes ago
LIST_DEPTH = 3                # newest N VODs checked per channel
YT_CATEGORY = "20"            # 20 = Gaming
GRAPH = "v25.0"               # Meta Graph API version

env = os.environ.get
GEMINI_KEY = env("GEMINI_API_KEY")
YT_PRIVACY = env("YT_PRIVACY") or "public"
GEMINI_MODELS = [m for m in [env("GEMINI_MODEL"), "gemini-3.8-flash", "gemini-3.6-flash", "gemini-2.5-flash"] if m]
HAVE_YT = all(env(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))
HAVE_IG = all(env(k) for k in ("IG_USER_ID", "IG_ACCESS_TOKEN"))

ROOT = Path(__file__).resolve().parent
WORK, OUT = ROOT / "work", ROOT / "out"
STATE = ROOT / "processed.json"


# ---------------- helpers ----------------
def sh(cmd, check=True):
    print("$", " ".join(str(c) for c in cmd)[:300], flush=True)
    return subprocess.run([str(c) for c in cmd], check=check, text=True)


def check(r, what):
    if not r.ok:
        raise RuntimeError(f"{what} failed: {r.status_code} {r.text[:500]}")
    return r


def load_state():
    s = json.loads(STATE.read_text()) if STATE.exists() else {}
    s.setdefault("done", [])
    s.setdefault("fails", {})
    return s


def save_state(s):
    STATE.write_text(json.dumps(s, indent=2))


def probe_duration(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def ts(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


# ---------------- finding VODs ----------------
def channels():
    p = ROOT / "channels.txt"
    if not p.exists():
        return []
    return [l.strip() for l in p.read_text().splitlines() if l.strip() and not l.startswith("#")]


def list_vods(channel_url):
    r = subprocess.run(["yt-dlp", "--flat-playlist", "--playlist-end", str(LIST_DEPTH), "--dump-json",
                        "--no-warnings", channel_url], capture_output=True, text=True)
    vods = []
    for line in r.stdout.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        url = d.get("url") or d.get("webpage_url")
        if url:
            vods.append({"id": str(d.get("id") or url), "url": url})
    if not vods:
        print(f"[warn] no VODs found for {channel_url}: {r.stderr[-300:]}")
    return vods


def vod_info(url):
    r = subprocess.run(["yt-dlp", "--dump-json", "--no-warnings", "--skip-download", url],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        print("[warn] could not read VOD info:", r.stderr[-400:])
        return None
    return json.loads(r.stdout.splitlines()[0])


def still_recording(info):
    t, d = info.get("timestamp"), info.get("duration")
    return bool(t and d and time.time() - (t + d) < WAIT_AFTER_STREAM_MIN * 60)


# ---------------- transcription ----------------
_M = None


def whisper():
    global _M
    if _M is None:
        from faster_whisper import WhisperModel
        _M = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _M


def transcribe(path, words=False):
    segs, _ = whisper().transcribe(str(path), vad_filter=True, beam_size=1, word_timestamps=words)
    return list(segs)


def transcript_text(segs):
    lines, start, buf = [], None, []
    for s in segs:
        if start is None:
            start = s.start
        buf.append(s.text.strip())
        if s.end - start >= 15:
            lines.append(f"[{int(start)}] {' '.join(buf)}")
            start, buf = None, []
    if buf:
        lines.append(f"[{int(start)}] {' '.join(buf)}")
    return "\n".join(lines)


# ---------------- Gemini: pick moments ----------------
PROMPT = """You are a short-form video editor. Below is a livestream transcript. Each line starts with [seconds from stream start].
Pick up to {n} of the most engaging moments: funny, shocking, hype, emotional, or a complete mini-story with a clear payoff.
Rules: each clip is {lo}-{hi} seconds long, starts at the beginning of a sentence, ends right after the payoff, clips must not overlap.
Return ONLY a JSON array. Each item: {{"start": <seconds>, "end": <seconds>, "title": "<catchy title, max 70 chars, no hashtags>", "hashtags": ["#tag1","#tag2","#tag3","#tag4","#tag5"], "score": <1-10>}}

TRANSCRIPT:
{t}
"""


def gemini(prompt):
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4}}
    for m in GEMINI_MODELS:
        for attempt in range(4):
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
                              headers={"x-goog-api-key": GEMINI_KEY}, json=body, timeout=300)
            if r.status_code == 404:
                print("[warn] Gemini model not found:", m)
                break
            if r.status_code in (429, 500, 503):
                time.sleep(30 * (attempt + 1))
                continue
            check(r, "Gemini")
            parts = r.json()["candidates"][0]["content"]["parts"]
            txt = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            txt = re.sub(r"^```(?:json)?|```$", "", txt).strip()
            return json.loads(txt)
    raise RuntimeError("Gemini: no model worked (set the GEMINI_MODEL repo variable to a current free Flash model)")


def pick_clips(text, total):
    raw = gemini(PROMPT.format(n=MAX_CLIPS_PER_VOD + 2, lo=CLIP_MIN, hi=CLIP_MAX, t=text))
    if isinstance(raw, dict):
        raw = raw.get("clips", [])

    def score(c):
        try:
            return -float(c.get("score", 0))
        except (TypeError, ValueError):
            return 0

    clips = []
    for c in sorted(raw, key=score):
        try:
            s, e = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if e - s > CLIP_MAX:
            e = s + CLIP_MAX
        if s < 0 or e - s < 15 or (total and s >= total):
            continue
        if any(s < x["end"] and e > x["start"] for x in clips):
            continue
        tags = [("#" + str(t).lstrip("#").replace(" ", "")) for t in (c.get("hashtags") or [])][:6]
        clips.append({"start": s, "end": e, "title": str(c.get("title", "Stream highlight"))[:90], "tags": tags})
        if len(clips) >= MAX_CLIPS_PER_VOD:
            break
    return clips


# ---------------- cutting + rendering ----------------
def download_audio(url, dest_dir):
    sh(["yt-dlp", "-f", "Audio_Only/ba/worst", "-x", "--audio-format", "mp3",
        "--postprocessor-args", "ffmpeg:-ac 1 -ar 16000",
        "-o", dest_dir / "audio.%(ext)s", url])
    return dest_dir / "audio.mp3"


def download_segment(url, start, end, dest):
    sh(["yt-dlp", "-f", "best[height<=720]/best", "--download-sections", f"*{start:.1f}-{end:.1f}",
        "--force-keyframes-at-cuts", "--remux-video", "mp4", "-o", dest, url])
    if dest.exists():
        return dest
    found = list(dest.parent.glob(dest.stem + ".*"))
    if not found:
        raise RuntimeError("segment download produced no file")
    return found[0]


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,84,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,8,60,60,960,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(words, path):
    ev = []
    for i in range(0, len(words), 3):
        g = words[i:i + 3]
        start, end = g[0].start, g[-1].end
        if i + 3 < len(words):
            end = max(end, min(words[i + 3].start, end + 0.4))
        txt = re.sub(r"[{}\\]", "", " ".join(w.word.strip() for w in g)).upper()
        ev.append(f"Dialogue: 0,{ts(start)},{ts(end)},Default,,0,0,0,,{txt}")
    path.write_text(ASS_HEADER + "\n".join(ev) + "\n", encoding="utf-8")


def render(seg, ass, bg, out):
    dur = min(probe_duration(seg), 60)
    bg_len = probe_duration(bg)
    ss = random.uniform(0, max(0, bg_len - dur - 1))
    sub = f"[v]ass={ass}[vout]" if ass else "[v]null[vout]"
    fc = ("[1:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30[bg];"
          "[0:v]scale=1080:-2,setsar=1,fps=30[fg];"
          "[bg][fg]overlay=0:300:shortest=1[v];" + sub)
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", seg, "-stream_loop", "-1", "-ss", f"{ss:.2f}", "-i", bg,
        "-filter_complex", fc, "-map", "[vout]", "-map", "0:a:0?", "-t", f"{dur:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out])


# ---------------- publishing ----------------
def youtube_upload(path, title, desc, tags):
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": env("YT_CLIENT_ID"), "client_secret": env("YT_CLIENT_SECRET"),
        "refresh_token": env("YT_REFRESH_TOKEN"), "grant_type": "refresh_token"}, timeout=60)
    token = check(r, "YouTube token refresh").json()["access_token"]
    meta = {"snippet": {"title": title[:100], "description": desc, "tags": [t.lstrip("#") for t in tags][:15],
                        "categoryId": YT_CATEGORY},
            "status": {"privacyStatus": YT_PRIVACY, "selfDeclaredMadeForKids": False}}
    size = path.stat().st_size
    r = requests.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                               "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                      data=json.dumps(meta), timeout=60)
    session = check(r, "YouTube start upload").headers["Location"]
    with open(path, "rb") as f:
        r = requests.put(session, data=f, headers={"Content-Type": "video/mp4", "Content-Length": str(size)},
                         timeout=900)
    return check(r, "YouTube upload").json()["id"]


def instagram_reel(path, caption):
    tok, uid = env("IG_ACCESS_TOKEN"), env("IG_USER_ID")
    base = f"https://graph.facebook.com/{GRAPH}"
    r = requests.post(f"{base}/{uid}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200], "access_token": tok},
        timeout=60)
    cid = check(r, "IG create container").json()["id"]
    size = path.stat().st_size
    with open(path, "rb") as f:
        r = requests.post(f"https://rupload.facebook.com/ig-api-upload/{GRAPH}/{cid}",
                          headers={"Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(size)},
                          data=f, timeout=900)
    check(r, "IG upload bytes")
    for _ in range(60):
        s = requests.get(f"{base}/{cid}", params={"fields": "status_code,status", "access_token": tok},
                         timeout=60).json()
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"IG processing failed: {s}")
        time.sleep(10)
    else:
        raise RuntimeError("IG processing timed out")
    r = requests.post(f"{base}/{uid}/media_publish", data={"creation_id": cid, "access_token": tok}, timeout=60)
    return check(r, "IG publish").json()["id"]


# ---------------- per-VOD flow ----------------
def process_vod(url, info):
    vid = re.sub(r"\W+", "_", str(info.get("id", "vod")))
    w = WORK / vid
    w.mkdir(parents=True, exist_ok=True)
    backgrounds = sorted((ROOT / "backgrounds").glob("*.mp4"))
    if not backgrounds:
        raise RuntimeError("Put at least one .mp4 in the backgrounds/ folder")

    print("== downloading audio + transcribing")
    segs = transcribe(download_audio(url, w))
    text = transcript_text(segs)
    if len(text) < 300:
        print("[warn] almost no speech detected, skipping")
        return False

    print("== asking Gemini for the best moments")
    clips = pick_clips(text, info.get("duration"))
    print(f"== {len(clips)} clips selected")

    posted = 0
    for i, c in enumerate(clips, 1):
        try:
            print(f"== clip {i}: {c['title']} ({c['start']:.0f}-{c['end']:.0f}s)")
            seg = download_segment(url, c["start"], c["end"], w / f"seg{i}.mp4")
            wav = w / f"seg{i}.wav"
            sh(["ffmpeg", "-y", "-loglevel", "error", "-i", seg, "-vn", "-ac", "1", "-ar", "16000", wav])
            words = [x for s in transcribe(wav, words=True) for x in (s.words or [])]
            ass = None
            if words:
                ass = w / f"seg{i}.ass"
                build_ass(words, ass)
            final = OUT / f"{vid}_{i}.mp4"
            render(seg, ass, random.choice(backgrounds), final)

            caption = f"{c['title']}\n\n{' '.join(c['tags'])}"
            if HAVE_YT:
                try:
                    yid = youtube_upload(final, c["title"], caption + " #Shorts", c["tags"])
                    print("   YouTube OK: https://youtube.com/shorts/" + yid)
                    posted += 1
                except Exception as e:
                    print("   [YouTube FAILED]", e)
            if HAVE_IG:
                try:
                    print("   Instagram OK, media id", instagram_reel(final, caption))
                    posted += 1
                except Exception as e:
                    print("   [Instagram FAILED]", e)
        except Exception as e:
            print(f"   [clip {i} FAILED]", e)
    return posted > 0


def main():
    manual = (env("VIDEO_URL") or "").strip()
    WORK.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    state = load_state()

    if manual:
        targets = [{"id": manual, "url": manual, "force": True}]
    else:
        targets = [v for ch in channels() for v in list_vods(ch)]
        if not STATE.exists():  # first scheduled run: ignore old VODs, only clip new streams from now on
            state["done"] = [v["id"] for v in targets]
            save_state(state)
            print(f"First run: marked {len(state['done'])} existing VODs as seen. New streams get clipped from now on.")
            return

    handled = 0
    for t in targets:
        if handled >= MAX_VODS_PER_RUN:
            break
        if not t.get("force") and t["id"] in state["done"]:
            continue
        info = vod_info(t["url"])
        if not info:
            continue
        if not t.get("force") and still_recording(info):
            print("Stream still live or just ended, will retry next run:", t["url"])
            continue
        handled += 1
        try:
            ok = process_vod(t["url"], info)
        except Exception as e:
            print("[VOD FAILED]", e)
            ok = False
        if not t.get("force"):
            if ok:
                state["done"].append(t["id"])
            else:
                n = state["fails"].get(t["id"], 0) + 1
                state["fails"][t["id"]] = n
                if n >= 3:
                    state["done"].append(t["id"])  # give up after 3 failed attempts
            save_state(state)
        shutil.rmtree(WORK, ignore_errors=True)
        WORK.mkdir()
    if handled == 0:
        print("Nothing new to process.")


if __name__ == "__main__":
    main()
