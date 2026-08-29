# 🎶 𝑨𝒑𝒆𝒙 𝑽𝒊𝒃𝒆𝒔 .ᐟ.ᐟ — Telegram Music Bot

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11-blue?style=for-the-badge&logo=python" />
  <img src="https://img.shields.io/badge/Pyrogram-2.0.106-orange?style=for-the-badge" />
  <img src="https://img.shields.io/badge/PyTgCalls-0.9.32-green?style=for-the-badge" />
  <img src="https://img.shields.io/badge/MongoDB-Atlas-brightgreen?style=for-the-badge&logo=mongodb" />
</p>

<p align="center">
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
| `YT_COOKIES` | Base64-encoded cookies.txt for YouTube | ❌ |
| `AUTOPLAY` | Enable autoplay by default (default: true) | ❌ |

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

### 🎧 Voice Chat
| Command | Description |
|---------|-------------|
| `/joinvc` | Join the group voice chat |
| `/leavevc` | Leave the group voice chat |
| `/autoend` | Leave when the voice chat becomes empty |

---

## 🚢 Manual Setup

```bash
git clone <your-private-repo-url>
cd Clone-musicbot
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
