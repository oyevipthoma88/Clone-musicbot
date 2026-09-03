# Clone/music-bot research notes

Reviewed public repositories before changing ApexVibe. No credentials or source code were copied.

| Repository | Verified pattern relevant to ApexVibe | Decision |
|---|---|---|
| [AsmSafone/MusicPlayer](https://github.com/AsmSafone/MusicPlayer) | Pyrogram + PyTgCalls, Heroku/app.json deployment, env-based API/token/session configuration, direct streaming while downloading, admin-sensitive controls, thumbnails | Retain lightweight env configuration, direct-first playback, and music-only scope. |
| [CertifiedCoders/AnnieXMusic](https://github.com/CertifiedCoders/AnnieXMusic) | Pyrogram + PyTgCalls, Heroku/Docker setup, explicit logger ID and cookie URL variables, setup pre-checks | Retain pre-verification and private logger requirements; never expose raw cookies/tokens. |
| [hasindu-nagolla/HasiiMusicBot](https://github.com/hasindu-nagolla/HasiiMusicBot) | Environment-only configuration, logger group, assistant string session, Docker/restart-oriented deployment, admin authorization | Retain env-driven runtime and bounded cleanup; use persistent registry for clone metadata. |
| [gabrielmaialva33/flora-music-bot](https://github.com/gabrielmaialva33/flora-music-bot) | Heroku/container manifest, logger channel, cookies handling, multi-instance architecture, Mongo-backed persistence in a larger project | Borrow only the separation of runtime config and persistent records; do not add its unrelated operational/dev surface. |
| [TeamYukki/YukkiMusicBot](https://github.com/TeamYukki/Yukkimusicbot) | Established Pyrogram/PyTgCalls voice-chat music-bot pattern with Heroku-oriented configuration | Use only as a broad compatibility reference; ApexVibe remains a small single-file music worker. |

The public projects consistently keep deployment credentials in environment/configuration rather than audit messages. ApexVibe will therefore persist encrypted clone records and send a single `<pre>` audit message with masked tokens, API-hash fingerprints, and session hashes rather than plaintext secrets. Heroku dyno restart persistence requires an external durable store or mounted persistent volume; `/tmp` and process memory are not durable.
