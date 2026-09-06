# 🎶 Apex Vibes — Telegram Music Bot

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11-blue?style=for-the-badge&logo=python" />
  <img src="https://img.shields.io/badge/Pyrogram-2.0.106-orange?style=for-the-badge" />
  <img src="https://img.shields.io/badge/PyTgCalls-2.3.3-green?style=for-the-badge" />
  <img src="https://img.shields.io/badge/MongoDB-Atlas-brightgreen?style=for-the-badge&logo=mongodb" />
</p>

<p align="center">
  <b>Premium Telegram Music Bot • YouTube Streaming • AutoPlay • Lyrics • Colored Mini-App Controls</b>
</p>

<p align="center">
  <img src="assets/apex-vibes-hero.jpg" alt="Apex Vibes live music experience" width="520" />
</p>

<p align="center">
  <img src="assets/live-wave.svg" alt="Animated Apex Vibes audio waveform" width="720" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/%E2%9A%A1%20Startup%20target-3%E2%80%937s-7c3aed?style=for-the-badge" alt="Startup target 3 to 7 seconds" />
  <img src="https://img.shields.io/badge/%F0%9F%9B%A1%20Startup%20budget-5%E2%80%9310s-0ea5e9?style=for-the-badge" alt="Bounded startup budget 5 to 10 seconds" />
  <img src="https://img.shields.io/badge/%F0%9F%8E%B6%20Mode-Audio%20%7C%20Video-f59e0b?style=for-the-badge" alt="Audio and video playback" />
</p>

<p align="center">
  <i>Search. Tap play. Join the voice chat. Let the music flow.</i>
</p>

---

## 🚀 Deploy on Heroku

[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy)

> Click the button above → Fill in env vars → Deploy! Bot will start automatically.

---

## ⚙️ Required Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `API_ID` | From [my.telegram.org](https://my.telegram.org) | ✅ |
| `API_HASH` | From [my.telegram.org](https://my.telegram.org) | ✅ |
| `BOT_TOKEN` | From [@BotFather](https://t.me/BotFather) | ✅ |
| `MONGO_DB_URI` | MongoDB Atlas connection string | ✅ |
| `STRING_SESSION` | Pyrogram string session of assistant | ✅ |
| `OWNER_ID` | Your Telegram numeric user ID | ✅ |
| `LOG_GROUP_ID` | Private group ID for error logs | ✅ |
| `OWNER_NAME` | Alias shown in play cards (default: Maestro) | ❌ |
| `BOT_USERNAME` | Your bot's @username | ❌ |
| `GENIUS_API_TOKEN` | From [genius.com/api-clients](https://genius.com/api-clients) | ❌ |
| `YT_COOKIES` | Base64-encoded cookies.txt for YouTube | ❌ |
| `AUTOPLAY` | Enable autoplay by default (default: true) | ❌ |
| `VIDEO_QUALITY` | `/vplay` output quality: 720p default; 1080p/480p/360p supported | ❌ |
| `PLAY_PROBE_TIMEOUT` | Seconds allowed for direct stream handoff before fallback (default: 7) | ❌ |
| `DIRECT_VIDEO_STREAM` | Direct YouTube video streaming; required for multi-gigabyte movies (default: true) | ❌ |
| `EARLY_AUDIO_HANDOFF` | Growing-file audio handoff; keep false on cloud hosts (default: false on cloud) | ❌ |
| `TG_PROXY_CACHE_MB` | Global RAM budget for Telegram large-media proxy chunks (default: 96) | ❌ |

### Production capacity policy

The worker is designed to keep playback reliable under bounded concurrency, not
to promise 100,000 simultaneous voice chats on one process. A single Heroku
worker has finite CPU, RAM, Telegram RPC throughput, YouTube CDN bandwidth, and
voice-call session capacity. Use multiple independently sharded bot workers
and a durable queue/state layer for very large public deployments; do not raise
the download or proxy limits blindly. `DIRECT_VIDEO_STREAM=true` is mandatory
for 3GB+/multi-hour YouTube video because the complete file must never be
downloaded to the dyno filesystem. Tagged Telegram media uses the range proxy
and reads only requested chunks, with a global RAM cache cap.

### MongoDB runtime policy
The live bot uses **only** `MONGO_DB_URI`, which must point to the new MongoDB Atlas cluster. Do not configure any second MongoDB URI in Heroku or in the bot environment. Never commit or paste a MongoDB URI into source control or chat.


---

## 📋 All Commands

### 🎵 Music
| Command | Description |
|---------|-------------|
| `/play [song/url]` | Play from YouTube |
| `/vplay [song/url]` | Video stream |
| `/queue` or `/q` | Show current queue |
| `/skip` or `/s` | Skip current song |
| `/pause` | Pause playback |
| `/resume` | Resume playback |
| `/stop`, `/end` | Stop music + clear queue |
| `/seek [sec]` | Seek forward N seconds |
| `/rewind [sec]` | Rewind N seconds |
| `/np` | Now playing info |
| `/shuffle` | Shuffle queue |
| `/clearqueue` | Clear entire queue |
| `/remove [pos]` | Remove track at position |
| `/search [query]` | Inline YouTube search |

### ⚙️ Settings
| Command | Description |
|---------|-------------|
| `/volume [1-200]` | Set volume level |
| `/mute` | Mute audio |
| `/unmute` | Unmute (restores to 100) |
| `/loop` | Loop current song |
| `/loopall` | Loop entire queue |
| `/noloop` | Disable loop |
| `/speed [0.5-2.0]` | Playback speed |
| `/autoplay on/off` | Toggle autoplay |

### ℹ️ Info
| Command | Description |
|---------|-------------|
| `/lyrics [song]` | Fetch lyrics from Genius |
| `/ping` | Bot latency |
| `/stats` | Bot statistics (uptime, RAM, chats) |
| `/about` | About Apex Vibes (anonymous) |
| `/start` | Welcome message |
| `/help` | Inline categorized help |

### 👑 Admin (group admins only)
| Command | Description |
|---------|-------------|
| `/auth [user]` | Authorize user to use bot commands |
| `/unauth [user]` | Remove authorization |
| `/authlist` | List authorized users |
| `/ban [user]` | Ban user from using bot |
| `/unban [user]` | Unban user |

### 🔒 Owner (hidden from /help)
| Command | Description |
|---------|-------------|
| `/setpic` | Set the DM /start picture (send/reply to a photo). Saved permanently to GitHub — see `GITHUB_TOKEN`/`GITHUB_REPO`. |
| `/delpic` | Remove the custom /start picture |
| `/setwelcomepic` | Set the picture shown when the bot joins a new group (send/reply to a photo). Saved permanently to GitHub too. |
| `/delwelcomepic` | Remove the custom group-welcome picture |
| `/reboot` | Full process restart |
| `/restart` | Same as /reboot |
| `/reload` | Hot-reload all plugins (no restart) |
| `/update` | Git pull + restart |
| `/shell [cmd]` | Run shell command |
| `/eval [code]` | Evaluate Python code |
| `/logs` | Send bot log file |
| `/stats` | Full stats (owner sees more) |
| `/gban [user]` | Global ban user |
| `/ungban [user]` | Remove global ban |
| `/broadcast` | Mass message all chats |
| `/chatlist` | List all served chats |
| `/maintenance on/off` | Toggle maintenance mode |

---

## 🚢 Manual Setup

```bash
git clone <your-private-repo-url>
cd Apex Vibes_music
pip install -r requirements.txt
cp .env.example .env
# Fill in .env values
python -m melody
```

---

## 🔒 Privacy

- Owner identity is **never** exposed in any user-facing message
- All errors → `LOG_GROUP_ID` only (private)
- No real names, GitHub details, or server info shown to users
- `.env` is in `.gitignore` — never committed

---

*Made with 💛 by an anonymous developer 🌑*
