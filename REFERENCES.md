# External design references

These public repositories were consulted for feature and deployment patterns only; ApexVibe uses a focused adaptation rather than copying unrelated code.

| Repository | URL | Observed relevant pattern |
|---|---|---|
| HasiiMusicBot | https://github.com/hasindu-nagolla/HasiiMusicBot | Python + Pyrogram + PyTgCalls + FFmpeg music architecture; environment-based deployment; queue and playback controls. |
| ShrutiMusic | https://github.com/NoxxOP/ShrutiMusic | Heroku template/app.json structure, direct YouTube search/play flow, and inline-control-oriented music UI. |
| tg-music-bot | https://github.com/mehrshadharry/tg-music-bot | Small player/handler separation and bounded feature scope. |
| AnnieXMusic | https://github.com/CertifiedCoders/AnnieXMusic | Practical Heroku manifests, cookies configuration, and music command organization. |
| AuraMusic | https://github.com/TeamAuraMusic/AuraMusic | Cache-first repeat playback concept. |

The references were accessed on 2026-08-27. No credentials, cookies, tokens, or private URLs were copied into ApexVibe.
