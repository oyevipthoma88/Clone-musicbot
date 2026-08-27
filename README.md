# ApexVibe Music Bot

ApexVibe is a deliberately small Telegram voice-chat music bot. It exposes only two public commands:

- `/play <song name or YouTube URL>`
- `/skip`

The runtime contains no autoplay, playlist scanner, social commands, admin suite, startup recovery, MongoDB/GridFS, or unrelated plugins. That keeps the command dispatcher, memory footprint, and playback state easy to reason about on a small Heroku worker.

## Deploy to Heroku

[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://dashboard.heroku.com/new?template=https://github.com/oyevipthoma88/Clone-musicbot)

The repository must be visible to the Heroku account for the template button to read it. If the repository remains private, deploy it from Heroku after granting GitHub access, or change the repository visibility only if you are comfortable publishing the source.

The worker requires:

| Variable | Required | Purpose |
|---|---:|---|
| `API_ID` | Yes | Telegram API ID from `my.telegram.org` |
| `API_HASH` | Yes | Telegram API hash |
| `BOT_TOKEN` | Yes | BotFather token |
| `STRING_SESSION` | Yes | Assistant account session used by PyTgCalls |
| `YT_COOKIES` | No | Netscape `cookies.txt` text or base64 value |
| `COOKIE_URL` | No | Private raw cookies URL, used only when `YT_COOKIES` is empty |
| `YOUTUBE_API_KEY` | No | YouTube Data API v3 key for fast search |

The assistant account and the bot must be members of the group. The assistant needs permission to join/manage the voice chat, and a voice chat must be active before `/play` can stream.

## Fast and safe playback design

A direct YouTube audio URL is attempted first. If it is rejected, ApexVibe performs one bounded audio download under a global one-slot lock, then plays the completed cache file. Completed files are kept under a capped `/tmp/apexvibe-cache` directory and old files are evicted by access time. No growing `.part` file is handed to PyTgCalls, and no full movie/video pipeline is included in this music-only build.

Each chat has a generation counter. A new `/skip` cancels the previous play task and advances only the current queue state; an older resolver cannot call PyTgCalls after it has been superseded. `/skip` acknowledges immediately and performs the voice transition in a tracked background task.

## Local run

```bash
sudo apt-get install ffmpeg
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill .env, then:
python apexvibe.py
```

## Design references

The implementation was informed by publicly available patterns in current Python/Pyrogram/PyTgCalls music bots, but the code here is a fresh minimal implementation rather than a wholesale copy:

- [HasiiMusicBot](https://github.com/hasindu-nagolla/HasiiMusicBot) — Pyrogram/PyTgCalls/FFmpeg baseline and environment-based deployment.
- [ShrutiMusic](https://github.com/NoxxOP/ShrutiMusic) — Heroku template structure and direct YouTube search/play flow.
- [tg-music-bot](https://github.com/mehrshadharry/tg-music-bot) — small player/handler separation and bounded feature scope.
- [AnnieXMusic](https://github.com/CertifiedCoders/AnnieXMusic) — practical deployment manifests and YouTube cookie configuration.
- [AuraMusic](https://github.com/TeamAuraMusic/AuraMusic) — cache-first playback concept for repeat requests.

Those repositories advertise different feature sets and performance claims; ApexVibe intentionally keeps only the patterns relevant to safe fast playback and does not promise a universal sub-five-second start for blocked YouTube sources.

## License

This project is released under the MIT License. The third-party libraries retain their own licenses.
