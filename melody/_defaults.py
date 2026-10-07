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


# ═════════════════════════════════════════════════════════════════════════════
# ⚡ ShrutiBots YT API root fixes (active only when SHRUTI_API_KEY is set)
#   1. DOWNLOAD_HARD_TIMEOUT=4 was CANCELLING good API downloads (3-8s) ->
#      YouTube got marked "blocked" -> wrong JioSaavn song. Give API its time.
#   2. DIRECT_STREAM=true above made every /play waste 5-7s on the blocked
#      yt-dlp/Invidious CDN race. With the API, skip it (FORCE_DIRECT_STREAM=1
#      re-enables it).
#   3. Long /vplay (>15 min) was direct-CDN only -> always failed on Heroku.
#   4. Startup + periodic "API ACTIVE / INACTIVE" log lines.
# ═══════════════════════════════════════════════════════════════════════════
def _shruti_on() -> bool:
    return bool((os.environ.get("SHRUTI_API_KEY") or os.environ.get("SHRUTI_API_KEYS") or "").strip())


if _shruti_on():
    try:
        _st = float(os.environ.get("SHRUTI_TIMEOUT", "60") or 60)
    except ValueError:
        _st = 60.0
    try:
        _ht = float(os.environ.get("DOWNLOAD_HARD_TIMEOUT", "4") or 4)
    except ValueError:
        _ht = 4.0
    os.environ["DOWNLOAD_HARD_TIMEOUT"] = str(max(_ht, min(_st, 90.0) + 5.0))
    os.environ.setdefault("VIDEO_DIRECT_ONLY_SECONDS", "3600")
    if float(os.environ.get("VIDEO_DIRECT_ONLY_SECONDS") or 0) < 3600:
        os.environ["VIDEO_DIRECT_ONLY_SECONDS"] = "3600"

    import sys as _sys
    import threading as _th
    import time as _time

    def _patch_ytdl(mod) -> None:
        orig = getattr(mod, "should_try_direct_stream", None)
        if orig is None or getattr(orig, "_shruti", False):
            return

        def should_try_direct_stream() -> bool:
            if os.getenv("FORCE_DIRECT_STREAM", "0").strip().lower() in {"1", "true", "yes", "on"}:
                return orig()
            return False

        should_try_direct_stream._shruti = True  # type: ignore[attr-defined]
        mod.should_try_direct_stream = should_try_direct_stream

    def _patch_main(mod) -> None:
        orig = getattr(mod, "send_startup_log", None)
        if orig is None or getattr(orig, "_shruti", False):
            return

        async def send_startup_log(bot, assistant, *a, **k):
            try:
                await orig(bot, assistant, *a, **k)
            finally:
                try:
                    from melody.core import shruti_api as _sh
                    await _sh.health_check()
                    _sh.start_monitor()
                    await _sh.send_status(bot, assistant)
                except Exception as exc:  # noqa: BLE001
                    try:
                        from melody.logging import LOGGER
                        LOGGER.warning("ShrutiAPI status log failed: %r", exc)
                    except Exception:  # noqa: BLE001
                        pass

        send_startup_log._shruti = True  # type: ignore[attr-defined]
        mod.send_startup_log = send_startup_log

    def _watch() -> None:
        done_y = done_m = False
        end = _time.time() + 600
        while _time.time() < end and not (done_y and done_m):
            y = _sys.modules.get("melody.core.ytdl")
            if not done_y and y is not None and hasattr(y, "should_try_direct_stream"):
                _patch_ytdl(y)
                done_y = True
            m = _sys.modules.get("__main__")
            if not done_m and m is not None and hasattr(m, "send_startup_log"):
                _patch_main(m)
                done_m = True
            _time.sleep(0.02)

    _th.Thread(target=_watch, name="shruti-patch", daemon=True).start()
