# Clipping

> Automatically turns Twitch livestream VODs into vertical short-form clips with captions and gameplay backgrounds, then posts them to YouTube Shorts and Instagram Reels.

![Purpose](https://img.shields.io/badge/purpose-educational-blue)
![Risk](https://img.shields.io/badge/use-at%20your%20own%20risk-orange)

---

## Table of Contents

- [Disclaimer](#️-disclaimer)
- [About](#-about)
- [Features](#-features)
- [How it works](#-how-it-works)
- [Requirements](#️-requirements)
- [Installation](#-installation)
- [Configuration](#️-configuration)
- [Usage](#-usage)
- [Limitations](#-limitations)
- [Terms of Service](#-terms-of-service)
- [Privacy Policy](#-privacy-policy)
- [Contributing](#-contributing)
- [License](#-license)

---

## ⚠️ Disclaimer

This project is created **strictly for educational and learning purposes**.

- It is provided **"as is"**, without warranty of any kind.
- **Use it at your own risk.** The author is not responsible for any damage, data loss, account suspension, legal issues, or other consequences resulting from the use or misuse of this project.
- You are solely responsible for ensuring your use complies with all applicable laws and the terms of any third-party platform or content involved (Twitch, Kick, YouTube, Instagram, Google, Zernio).
- Only use this project with content you own or have permission to use. Clipping and reposting another streamer's content without their permission may break copyright law and platform rules.

---

## 📖 About

Clipping is a GitHub Actions pipeline that watches a streamer's Twitch VODs, finds the most engaging moments with AI, and publishes them as vertical clips. It was built as a learning project that combines speech-to-text, an LLM, video editing with FFmpeg, and a social-posting API in one automated workflow.

You can learn from it how to chain these pieces together: transcribing hours of audio with Whisper, asking an LLM to pick timestamps from a transcript, cutting and compositing video with FFmpeg, and scheduling and persisting state in GitHub Actions.

## ✨ Features

- **Auto-detection:** checks the newest VODs of each channel in `channels.txt` every 6 hours and skips ones already handled.
- **Waits for streams to finish:** ignores VODs of streams that are live or ended less than 30 minutes ago.
- **AI clip selection:** Whisper transcribes the stream and Gemini picks up to 4 moments (25-59 seconds each) with a title and hashtags.
- **9:16 rendering:** each clip is placed over a random looping video from `backgrounds/`, with large word-by-word captions.
- **Auto-posting:** clips are published to YouTube Shorts and Instagram Reels through Zernio.
- **Failure handling:** one failed clip doesn't stop the others, and a VOD is retried up to 3 times before being given up on.
- **Artifacts:** finished clips are kept for 3 days as GitHub Actions artifacts, for manual posting elsewhere (for example TikTok).

## 🔄 How it works

1. The workflow runs on a schedule (every 6 hours) or manually with a VOD link.
2. `pipeline.py` lists the newest VODs per channel with `yt-dlp` and picks one that is new and finished.
3. The audio is downloaded and transcribed locally on the runner with `faster-whisper`.
4. The transcript text is sent to Gemini, which returns the best moments as JSON.
5. For each moment, the segment is downloaded, transcribed again for word timings, and rendered with FFmpeg over a background video.
6. `zernio_publish.py` uploads the clip to Zernio, which posts it to YouTube and Instagram.
7. `processed.json` is committed back to the repo so the same VOD isn't processed twice.

## 🛠️ Requirements

- A GitHub account (the pipeline runs on GitHub Actions; Python 3.12 on `ubuntu-latest`)
- A [Google AI Studio](https://aistudio.google.com/apikey) API key for Gemini
- A [Zernio](https://zernio.com) account with your YouTube channel and an Instagram **Business or Creator** account connected (personal Instagram accounts can't post through APIs)
- At least one `.mp4` background video in the `backgrounds/` folder

Installed automatically by the workflow: `ffmpeg`, `fonts-dejavu-core`, `yt-dlp`, `faster-whisper`, `requests`.

## 📦 Installation

```bash
git clone https://github.com/mac864501-alt/Clipping.git
cd Clipping
```

Then, in your own copy of the repository on GitHub:

1. **Add secrets** (Settings → Secrets and variables → Actions → Secrets):
   - `GEMINI_API_KEY`
   - `ZERNIO_API_KEY`
2. **Allow the workflow to save state** (Settings → Actions → General → Workflow permissions → *Read and write permissions*).
3. **Add a background video:** put one or more `.mp4` files in `backgrounds/`.
4. **Set the channel:** edit `channels.txt` and replace `CHANNEL_NAME` with the streamer's Twitch name.

## ⚙️ Configuration

**Settings in `pipeline.py`:**

| Setting | Default | Meaning |
|---|---|---|
| `MAX_VODS_PER_RUN` | 1 | VODs processed per workflow run |
| `MAX_CLIPS_PER_VOD` | 4 | Clips made per VOD |
| `CLIP_MIN`, `CLIP_MAX` | 25, 59 | Clip length in seconds |
| `WHISPER_MODEL` | `base` | `small` is more accurate but slower |
| `WAIT_AFTER_STREAM_MIN` | 30 | Minutes to wait after a stream ends |
| `LIST_DEPTH` | 3 | Newest VODs checked per channel |

**Optional repository variables** (Settings → Secrets and variables → Actions → Variables):

| Variable | Meaning |
|---|---|
| `GEMINI_MODEL` | Gemini model to try first |
| `YT_PRIVACY` | `public` (default), `unlisted`, or `private` |
| `ZERNIO_PLATFORMS` | Default `youtube,instagram` |
| `ZERNIO_YOUTUBE_ACCOUNT_ID` | Only needed if you have several YouTube accounts connected |
| `ZERNIO_INSTAGRAM_ACCOUNT_ID` | Only needed if you have several Instagram accounts connected |

If `ZERNIO_API_KEY` is not set, clips are still rendered and saved as run artifacts, but nothing is posted.

## 🚀 Usage

**Automatic mode:** nothing to do. The workflow runs every 6 hours. The first scheduled run only marks the existing VODs as seen, and clipping starts with the next new stream.

**Manual mode (good for testing):**

1. Open the **Actions** tab and select **clipper**.
2. Click **Run workflow** and paste a Twitch VOD URL into `video_url`.
3. Open the run and look for `[zernio]` lines in the log to see the posting result.

Repository layout:

```
.github/workflows/clip.yml   workflow (schedule + manual run)
pipeline.py                  main pipeline
zernio_publish.py            posting through Zernio
channels.txt                 channels to watch
backgrounds/                 background .mp4 files
processed.json               created automatically, tracks finished VODs
```

## 🚧 Limitations

- Only one VOD is processed per run. A second stream on the same day waits for the next run.
- The streamer must have past broadcasts (VODs) saved on Twitch.
- Kick support is experimental, because Kick blocks many cloud servers.
- Posting four clips back to back may hit Zernio rate or duplicate-post limits (HTTP 409), and the free plan may not cover the volume.
- Long streams take a while to transcribe on GitHub's free CPU runners. Private repositories also count against the monthly free Actions minutes.
- If the repository is public, `processed.json` is visible to everyone.

---

## 📄 Terms of Service

**Last updated:** November 12, 2026

By downloading, copying, running, modifying, or otherwise using this project ("the Software"), you agree to these terms. If you do not agree, do not use the Software.

### 1. Educational purpose
The Software is shared for educational and learning purposes only. It is not a commercial product or a service, and it is not intended for production or critical use.

### 2. Use at your own risk
You use the Software entirely at your own risk. You are responsible for any outcome of using it, including effects on your device, files, data, and accounts (including GitHub, YouTube, Instagram, Google, and Zernio accounts).

### 3. Acceptable use
You agree **not** to use the Software to:
- Infringe copyrights, trademarks, or other intellectual property rights.
- Download, clip, or repost streams or videos without the permission of the creator or rights holder.
- Violate the terms of service of any website, app, or platform, including Twitch, Kick, YouTube, Instagram, Google, and Zernio.
- Post spam, misleading content, or content that harasses or deceives others.
- Violate any law or regulation, or anyone's privacy.

### 4. Third-party content and services
The Software does not own, host, or control any third-party content. You are fully responsible for the content you process with it, for obtaining any permissions required, and for the posts published to your accounts.

### 5. No warranty
The Software is provided **"as is"** and **"as available"**, without warranties of any kind, whether express or implied, including merchantability, fitness for a particular purpose, accuracy, reliability, and non-infringement. The author does not guarantee that it will be error-free, secure, or uninterrupted, or that AI-selected clips, titles, or captions will be accurate or appropriate.

### 6. Limitation of liability
To the maximum extent permitted by law, the author and contributors are not liable for any direct, indirect, incidental, special, or consequential damages, including data loss, loss of profits, account bans, takedowns, or legal claims, arising from the use of or inability to use the Software, even if advised of the possibility of such damages.

### 7. Indemnification
You agree to hold harmless the author and contributors from any claims, damages, or expenses arising from your use or misuse of the Software or your violation of these terms.

### 8. No support
The author is under no obligation to provide support, updates, bug fixes, or maintenance.

### 9. Changes and termination
These terms may be updated at any time, and the "Last updated" date will change. Continued use after changes means you accept them. Your permission to use the Software ends automatically if you violate these terms.

### 10. Governing law
These terms are governed by the laws applicable in the author's jurisdiction, without regard to conflict-of-law rules.

---

## 🔒 Privacy Policy

**Last updated:** November 12, 2026

Clipping is an open-source educational project, not a commercial product or online service.

### 1. Data collection by the author
The author **does not collect, store, transmit, or sell any personal data** through this project. The author has no access to your repository, secrets, videos, or usage.

### 2. Where processing happens
The Software runs in **your own GitHub repository, on GitHub Actions runners**. Downloading, transcription (Whisper), and video rendering happen on those runners. Finished clips are kept as GitHub Actions artifacts for 3 days, and `processed.json` (a list of processed VOD IDs) is committed to your repository.

### 3. Third-party services
When you use the Software, data is sent to services you configure. These services have their own privacy policies and terms, which you are responsible for reviewing:
- **Twitch / Kick:** the Software downloads VOD audio and video segments from them through `yt-dlp`.
- **Google (Gemini API):** the text transcript of the stream is sent to Google to pick clip moments.
- **Zernio:** each finished clip, with its title, caption, and tags, is uploaded to Zernio.
- **YouTube and Instagram:** Zernio publishes the clips to your connected accounts.
- **GitHub:** hosts the repository, workflow runs, and artifacts.

The Software itself has no analytics, tracking, or advertising.

### 4. Your responsibility
You are responsible for the content you process, for keeping API keys and credentials in GitHub **Secrets** (never in code or public files), and for remembering that a public repository exposes its files, including `processed.json`, to everyone.

### 5. Changes
This policy may be updated from time to time. Changes will appear here with a new "Last updated" date.

### 6. Contact
Questions or concerns? Open an issue on this repository.

---

## 🤝 Contributing

Issues, suggestions, and pull requests are welcome.

## 📜 License

MIT License. Add a `LICENSE` file with the MIT text to the repository, or replace this line with the license you prefer.

---

**Reminder:** This project is for learning only. By using it, you agree to the Terms of Service above and accept full responsibility for how you use it.
