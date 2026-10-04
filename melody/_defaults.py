"""
Hardcoded defaults — melode ki tarah chalane ke liye.
Ye os.environ me values set karta hai agar pehle se set nahi hain.
__main__.py me sabse pehle import hota hai.
"""
import os

_DEFAULTS = {
    # Direct stream band — WARP IP-lock ki wajah se 403 aata hai
    "DIRECT_STREAM": "false",
    "DISABLE_DIRECT_STREAM": "1",
    "PROXY_DIRECT_STREAM": "false",

    # WARP (Melody-style userspace wireproxy)
    "USE_WARP": "true",
    "ENABLE_WARP": "0",           # purana warp-plus off (UDP fail)
    "WARP_IPV6": "false",         # IPv6 YouTube pe flagged hai
    "WARP_PROXY_PORT": "40001",

    # Fast fail
    "PLAY_START_BUDGET": "20",
    "RESOLVE_TIMEOUT": "3.0",
    "DIRECT_RESOLVE_MAX": "3.5",

    # Clients — kam-flagged
    "YT_PLAYER_CLIENTS": "tv,mweb",
}

for _k, _v in _DEFAULTS.items():
    if not os.environ.get(_k):
        os.environ[_k] = _v
