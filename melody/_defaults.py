"""
Hardcoded defaults — JioSaavn-primary mode (YouTube SABR-block bypass).
"""
import os

_DEFAULTS = {
    # ═══ Core timing (JioSaavn 1-2s me kaam karta hai) ═══
    "DOWNLOAD_HARD_TIMEOUT": "4",       # yt-dlp cap 4s
    "PLAY_START_BUDGET": "12",           # call.py budget

    # ═══ Disable YouTube-specific hacks (SABR wall) ═══
    "USE_WARP": "false",                 # WARP nahi chahiye
    "ENABLE_WARP": "0",
    "DIRECT_STREAM": "true",
    "DISABLE_DIRECT_STREAM": "0",
    "PROXY_DIRECT_STREAM": "false",
    "YT_WORKER_URL": "",                 # Worker off
    "COOKIES_URL": "",                   # Gist fetch off (JioSaavn cookies nahi chahiye)

    # ═══ yt-dlp — sirf metadata extraction ke liye ═══
    "YT_PLAYER_CLIENTS": "tv,mweb",
    "RESOLVE_TIMEOUT": "2.5",
    "DIRECT_RESOLVE_MAX": "3.0",
}

for _k, _v in _DEFAULTS.items():
    if not os.environ.get(_k):
        os.environ[_k] = _v
