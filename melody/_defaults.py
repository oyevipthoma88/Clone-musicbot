"""
Hardcoded defaults — sab Heroku config vars code me hi set.
__main__.py me sabse pehle import hota hai.
"""
import os

_DEFAULTS = {
    # Timeouts
    "DOWNLOAD_HARD_TIMEOUT": "15",   # 15s yt-dlp cap → Worker rescue
    "PLAY_START_BUDGET": "25",       # call.py ka 25s budget
    "RESOLVE_TIMEOUT": "3.0",
    "DIRECT_RESOLVE_MAX": "3.5",

    # Cloudflare Worker proxy (clean IP for YouTube)
    "YT_WORKER_URL": "https://yt-proxy.flirtingzero.workers.dev",

    # Direct stream band — WARP IP-lock ki wajah se 403 aata hai
    "DIRECT_STREAM": "false",
    "DISABLE_DIRECT_STREAM": "1",
    "PROXY_DIRECT_STREAM": "false",

    # WARP (Melody-style userspace wireproxy)
    "USE_WARP": "true",
    "ENABLE_WARP": "0",              # purana warp-plus off (UDP fail)
    "WARP_IPV6": "false",            # IPv6 YouTube pe flagged hai
    "WARP_PROXY_PORT": "40001",

    # Clients — kam-flagged
    "YT_PLAYER_CLIENTS": "tv,mweb",
}

for _k, _v in _DEFAULTS.items():
    if not os.environ.get(_k):
        os.environ[_k] = _v
