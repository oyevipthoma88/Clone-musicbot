# ApexVibe Music Bot

ApexVibe is a deliberately small Telegram voice-chat music bot. It contains only music features and essential playback controls:

`/play`, `/vplay`, `/cplay`, `/playforce`, `/vplayforce`, `/cvplay`, `/skip`, `/pause`, `/resume`, `/stop`, `/queue`, `/now`, `/clearqueue`, `/remove`, `/shuffle`, `/loop`, `/loopall`, `/noloop`, `/volume`, `/seek`, `/seekback`, `/rewind`, `/speed`, `/search`, `/playlist`, `/song`, `/download`, and `/help`.

The runtime contains no social commands, general administration suite, startup recovery, GridFS, or unrelated plugins. MongoDB is used only for encrypted clone-user records. Clone onboarding is parent-only and bounded; it does not add unrelated handlers to child music workers. That keeps the command dispatcher, memory footprint, and playback state easy to reason about on a small Heroku worker.

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
| `OWNER_USERNAME` | Yes | Owner username for informational links |
| `MONGO_DB_URI` | Yes | MongoDB URI for encrypted clone-user backup across restarts |
| `CLONE_USERS_LOG` | Yes | Private Telegram group/channel receiving one clone audit message per event |
| `YT_COOKIES` | No | Netscape `cookies.txt` text, base64 value, or private HTTPS URL |
| `YOUTUBE_API_KEY` | No | YouTube Data API v3 key for fast search |

The assistant account and the bot must be members of the group. The assistant needs permission to join/manage the voice chat, and a voice chat must be active before `/play` can stream.

## Fast and safe playback design

A direct YouTube audio URL is attempted first. If it is rejected, ApexVibe performs one bounded audio download under a global one-slot lock, then plays the completed cache file. Completed files are kept under a capped `/tmp/apexvibe-cache` directory and old files are evicted by access time. No growing `.part` file is handed to PyTgCalls, and no full movie/video pipeline is included in this music-only build.

Each chat has a generation counter. A new `/skip` cancels the previous play task and advances only the current queue state; an older resolver cannot call PyTgCalls after it has been superseded. `/skip` acknowledges immediately and performs the voice transition in a tracked background task. With `AUTOPLAY=true`, a single bounded related-track lookup starts only after the queue is empty; a manual `/play` cancels that lookup or stream before it can commit, so autoplay cannot replace a newer user request.

## Free clone setup

The parent bot exposes `/start`, `/clone`, `/tutorial`, and `/cancel` in private chat. The start screen includes **Make Your Own Music Bot**, **Create Free Music Bot**, and **Free Music Tutorial** buttons. Clone setup asks for the bot token, API ID, API hash, assistant string session, log group/channel ID, owner ID, and owner username one step at a time. If `-` is entered for API ID/hash, log ID, owner ID, or owner username, the parent values from app.json/runtime environment are used. Update/support settings, YouTube API v3, and YouTube cookies are inherited from the parent app.json/runtime environment rather than requested again.

The token is verified through Telegram `getMe`; the assistant session is verified with a short-lived in-memory Pyrogram client. Submitted credential messages are deleted on a best-effort basis. The audit log is exactly one formatted `<pre>` message containing the setup user, verified bot username, requested IDs, update/support values, and configuration status. Bot tokens and API hashes are masked; string sessions are represented only by a SHA-256 prefix; YouTube keys and cookies are represented only as configured/not configured. Credentials are encrypted at rest and are never written to the repository, audit message, or a plaintext file.

For restart persistence, fill the app.json variables `CLONE_USERS_LOG` and `MONGO_DB_URI`. Clone records are encrypted before being stored in the MongoDB `apexvibe.clone_users` collection using a key derived from the parent API hash and bot token. On parent startup, records marked `starting` or `active` are decrypted in memory and their child workers are restored. Keep the parent API hash and bot token unchanged if existing clone records must remain restorable. The log group/channel must be private and the parent bot must be able to send messages there.

A successful setup starts a bounded child ApexVibe worker with `CLONE_MODE=true`. `MAX_ACTIVE_CLONES` defaults to `2` to protect a small Heroku dyno. These child workers live only as long as the parent dyno; encrypted configuration survives a restart, but the worker must be recreated after restart. For durable independent bots at larger scale, each clone needs its own persistent deployment and secret store. The parent bot remains the music worker and does not register clone onboarding inside child workers.

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
