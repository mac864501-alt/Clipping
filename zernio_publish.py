"""Publish a finished clip to YouTube and Instagram through Zernio.

Replaces the old Upload-Post call. Needs one secret: ZERNIO_API_KEY.
Account IDs are found automatically from the accounts connected in Zernio.

Env vars
  ZERNIO_API_KEY                required
  ZERNIO_PLATFORMS              optional, default "youtube,instagram"
  ZERNIO_YOUTUBE_ACCOUNT_ID     optional override (only needed if you have several)
  ZERNIO_INSTAGRAM_ACCOUNT_ID   optional override
  YT_PRIVACY                    optional, public (default) / unlisted / private
"""
import os
import time

import requests

API = "https://zernio.com/api/v1"
PENDING = {"pending", "publishing", "processing", "scheduled", "queued", "uploading"}
DONE_OK = {"published", "success", "completed"}


def _call(method, path, **kw):
    """HTTP call with a few retries on 429 / 5xx."""
    headers = kw.pop("headers", {})
    headers["Authorization"] = f"Bearer {os.environ['ZERNIO_API_KEY']}"
    for attempt in range(4):
        r = requests.request(method, f"{API}{path}", headers=headers, timeout=120, **kw)
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(5 * (attempt + 1))
            continue
        return r
    return r


def enabled():
    return bool(os.environ.get("ZERNIO_API_KEY"))


def _platforms():
    raw = os.environ.get("ZERNIO_PLATFORMS") or "youtube,instagram"
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _account_ids(platforms):
    ids = {}
    for p in platforms:
        override = os.environ.get(f"ZERNIO_{p.upper()}_ACCOUNT_ID")
        if override:
            ids[p] = override
    missing = [p for p in platforms if p not in ids]
    if missing:
        r = _call("GET", "/accounts")
        r.raise_for_status()
        for acc in r.json().get("accounts", []):
            p = (acc.get("platform") or "").lower()
            if p in missing and p not in ids:
                ids[p] = acc["_id"]
    return ids


def _upload(video_path):
    size = os.path.getsize(video_path)
    name = os.path.basename(video_path)
    r = _call("POST", "/media/presign",
              json={"filename": name, "contentType": "video/mp4", "size": size})
    r.raise_for_status()
    info = r.json()
    with open(video_path, "rb") as f:
        up = requests.put(info["uploadUrl"], data=f,
                          headers={"Content-Type": "video/mp4"}, timeout=900)
    up.raise_for_status()
    return info["publicUrl"]


def _summarise(post):
    out = {}
    for p in post.get("platforms", []):
        out[p.get("platform")] = {
            "status": (p.get("status") or "").lower(),
            "url": p.get("platformPostUrl"),
            "error": p.get("error") or p.get("errorMessage"),
        }
    return out


def publish(video_path, title, caption, tags=None, wait_seconds=600):
    """Upload one clip and post it. Returns True if at least one platform published."""
    if not enabled():
        print("[zernio] ZERNIO_API_KEY not set: skipping upload (clip kept as artifact)")
        return False

    platforms = _platforms()
    ids = _account_ids(platforms)
    missing = [p for p in platforms if p not in ids]
    if missing:
        print(f"[zernio] no connected account found for: {', '.join(missing)}")
        platforms = [p for p in platforms if p in ids]
        if not platforms:
            return False

    public_url = _upload(video_path)

    entries = []
    for p in platforms:
        entry = {"platform": p, "accountId": ids[p]}
        if p == "youtube":
            entry["platformSpecificData"] = {
                "title": title[:100],
                "visibility": os.environ.get("YT_PRIVACY") or "public",
                "categoryId": "20",  # Gaming
                "madeForKids": False,
            }
        elif p == "instagram":
            entry["platformSpecificData"] = {"shareToFeed": True}
        entries.append(entry)

    body = {
        "content": caption[:2200],
        "mediaItems": [{"type": "video", "url": public_url}],
        "platforms": entries,
        "publishNow": True,
    }
    if tags:
        body["tags"] = [t[:100] for t in tags][:30]

    r = _call("POST", "/posts", json=body)
    if r.status_code >= 400:
        print(f"[zernio] create post failed {r.status_code}: {r.text[:500]}")
        return False
    data = r.json()
    post = data.get("post", data)
    post_id = post.get("_id")

    deadline = time.time() + wait_seconds
    result = _summarise(post)
    while post_id and time.time() < deadline and (
        not result or any(v["status"] in PENDING or not v["status"] for v in result.values())
    ):
        time.sleep(10)
        g = _call("GET", f"/posts/{post_id}")
        if g.status_code >= 400:
            print(f"[zernio] status check failed {g.status_code}: {g.text[:300]}")
            continue
        gd = g.json()
        result = _summarise(gd.get("post", gd))

    # Success if at least one platform published (or is still being processed by Zernio).
    # Counting a still-pending post as success avoids re-posting the same clip on a retry.
    ok = False
    for p, v in result.items():
        good = v["status"] in DONE_OK or v["status"] in PENDING
        ok = ok or good
        line = f"[zernio] {p}: {v['status'] or 'unknown'}"
        if v["url"]:
            line += f" -> {v['url']}"
        if v["error"]:
            line += f" ({v['error']})"
        print(line)
    return ok
