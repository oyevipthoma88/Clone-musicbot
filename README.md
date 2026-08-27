# ApexVibe Music Bot

ApexVibe is a deliberately small Telegram voice-chat music bot. It contains only music features and essential playback controls:

`/play`, `/vplay`, `/cplay`, `/playforce`, `/vplayforce`, `/cvplay`, `/skip`, `/pause`, `/resume`, `/stop`, `/queue`, `/now`, `/clearqueue`, `/remove`, `/shuffle`, `/loop`, `/loopall`, `/noloop`, `/volume`, `/seek`, `/seekback`, `/rewind`, `/speed`, `/search`, `/playlist`, `/song`, `/download`, and `/help`.

The runtime contains no social commands, general administration suite, startup recovery, MongoDB/GridFS, or unrelated plugins. Clone onboarding is parent-only and bounded; it does not add unrelated handlers to child music workers. That keeps the command dispatcher, memory footprint, and playback state easy to reason about on a small Heroku worker.

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
| `LOG_GROUP_ID` | Yes | Private log group/channel ID |
| `OWNER_ID` | Yes | Numeric owner ID |
| `OWNER_USERNAME` | No | Owner username for informational links |
| `UPDATE_CHANNEL` | No | Update channel username or link |
| `SUPPORT_GROUP` | No | Support group username or link |
| `SUPPORT_CHANNEL` | No | Support channel username or link |
| `AUTOPLAY` | No | Related-track autoplay when the queue is empty; default `true` |
| `YT_COOKIES` | No | Netscape `cookies.txt` text, base64 value, or private HTTPS URL |
| `YOUTUBE_API_KEY` | No | YouTube Data API v3 key for fast search |

The assistant account and the bot must be members of the group. The assistant needs permission to join/manage the voice chat, and a voice chat must be active before `/play` can stream.

## Fast and safe playback design

A direct YouTube audio URL is attempted first. If it is rejected, ApexVibe performs one bounded audio download under a global one-slot lock, then plays the completed cache file. Completed files are kept under a capped `/tmp/apexvibe-cache` directory and old files are evicted by access time. No growing `.part` file is handed to PyTgCalls, and no full movie/video pipeline is included in this music-only build.

Each chat has a generation counter. A new `/skip` cancels the previous play task and advances only the current queue state; an older resolver cannot call PyTgCalls after it has been superseded. `/skip` acknowledges immediately and performs the voice transition in a tracked background task. With `AUTOPLAY=true`, a single bounded related-track lookup starts only after the queue is empty; a manual `/play` cancels that lookup or stream before it can commit, so autoplay cannot replace a newer user request.

## Free clone setup

The parent bot exposes `/start`, `/clone`, `/tutorial`, and `/cancel` in private chat. The start screen includes **Make Your Own Music Bot**, **Create Free Music Bot**, and **Free Music Tutorial** buttons. Clone setup asks for the bot token, API ID, API hash, assistant string session, log group/channel ID, owner ID, owner username, update channel, support group/channel, YouTube API key, and YouTube cookies one step at a time.

The token is verified through Telegram `getMe`; the assistant session is verified with a short-lived in-memory Pyrogram client. Submitted credential messages are deleted on a best-effort basis. The audit log is one formatted message containing the setup user and non-secret IDs, while bot tokens are masked and string sessions are represented only by a SHA-256 prefix. Credentials are passed only through the child process environment and are not written to the repository, audit message, or a plaintext file.

A successful setup starts a bounded child ApexVibe worker with `CLONE_MODE=true`. `MAX_ACTIVE_CLONES` defaults to `2` to protect a small Heroku dyno. These child workers live only as long as the parent dyno; for durable independent bots, each clone needs its own persistent deployment and secret store. The parent bot remains the music worker and does not register clone onboarding inside child workers.

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

The implementation was informed by publicly available patterns in current Python/Pyrogram/PyTgCalls music bots, and the original Melody_music music layer was selectively ported and simplified rather than carrying over unrelated plugins:

- [HasiiMusicBot](https://github.com/hasindu-nagolla/HasiiMusicBot) — Pyrogram/PyTgCalls/FFmpeg baseline and environment-based deployment.
- [ShrutiMusic](https://github.com/NoxxOP/ShrutiMusic) — Heroku template structure and direct YouTube search/play flow.
- [tg-music-bot](https://github.com/mehrshadharry/tg-music-bot) — small player/handler separation and bounded feature scope.
- [AnnieXMusic](https://github.com/CertifiedCoders/AnnieXMusic) — practical deployment manifests and YouTube cookie configuration.
- [AuraMusic](https://github.com/TeamAuraMusic/AuraMusic) — cache-first playback concept for repeat requests.

Those repositories advertise different feature sets and performance claims; ApexVibe intentionally keeps only the patterns relevant to safe fast playback and does not promise a universal sub-five-second start for blocked YouTube sources.

## License

This project is released under the MIT License. The third-party libraries retain their own licenses.
