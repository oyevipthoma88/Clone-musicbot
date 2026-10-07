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


# ═══════════════════════════════════════════════════════════════════════════
# ⚡ ShrutiBots YT API root fixes (active only when SHRUTI_API_KEY is set)
#   1. API runs FIRST, OUTSIDE the download-slot semaphore and OUTSIDE the
#      4s yt-dlp hard cap. v1 of this fix raised the cap to 65s for
#      everything -> a hung yt-dlp fallback held a download slot for 65s and
#      every other /play queued behind it (1-2 min command replies).
#      yt-dlp keeps its fast 4s cap; the API gets its own SHRUTI_TIMEOUT.
#   2. Hang-rescue (JioSaavn race) never starts while the API is still
#      downloading -> no more wrong JioSaavn song beating the real one.
#   3. Skip the blocked direct-CDN race (FORCE_DIRECT_STREAM=1 re-enables).
#   4. Long /vplay (>15 min) allowed through the API.
#   5. Startup + periodic "API ACTIVE / INACTIVE" log lines.
# ═══════════════════════════════════════════════════════════════════════════
def _shruti_on() -> bool:
    return bool((os.environ.get("SHRUTI_API_KEY") or os.environ.get("SHRUTI_API_KEYS") or "").strip())


if _shruti_on():
    os.environ.setdefault("SHRUTI_TIMEOUT", "25")
    try:
        if float(os.environ.get("VIDEO_DIRECT_ONLY_SECONDS") or 0) < 3600:
            os.environ["VIDEO_DIRECT_ONLY_SECONDS"] = "3600"
    except ValueError:
        os.environ["VIDEO_DIRECT_ONLY_SECONDS"] = "3600"

    import asyncio as _asyncio
    import contextvars as _cv
    import sys as _sys
    import threading as _th
    import time as _time

    _SKIP = _cv.ContextVar("shruti_skip", default=False)
    _INFLIGHT: dict = {}

    def _budget() -> float:
        try:
            return min(90.0, float(os.environ.get("SHRUTI_TIMEOUT", "25"))) + 5.0
        except ValueError:
            return 30.0

    def _log(level, msg, *args):
        try:
            from melody.logging import LOGGER
            getattr(LOGGER, level)(msg, *args)
        except Exception:  # noqa: BLE001
            pass

    def _patch_shruti(sh) -> None:
        orig = getattr(sh, "download", None)
        if orig is None or getattr(orig, "_shruti", False):
            return

        async def download(*a, **k):
            if _SKIP.get():
                return None  # already tried by the fast path below
            return await orig(*a, **k)

        download._shruti = True  # type: ignore[attr-defined]
        sh.download = download
        sh._raw_download = orig

    def _patch_ytdl(mod) -> None:
        orig_direct = getattr(mod, "should_try_direct_stream", None)
        if orig_direct is not None and not getattr(orig_direct, "_shruti", False):
            def should_try_direct_stream() -> bool:
                if os.getenv("FORCE_DIRECT_STREAM", "0").strip().lower() in {"1", "true", "yes", "on"}:
                    return orig_direct()
                return False
            should_try_direct_stream._shruti = True  # type: ignore[attr-defined]
            mod.should_try_direct_stream = should_try_direct_stream

        orig_impl = getattr(mod, "_download_audio_impl", None)
        if orig_impl is not None and not getattr(orig_impl, "_shruti", False):
            async def _download_audio_impl(video_id, audio_only, tag, priority=0,
                                           requested_at=None, cancel_event=None,
                                           early_state=None):
                from melody.core import shruti_api as sh
                _patch_shruti(sh)
                valid = getattr(mod, "is_valid_video_id", lambda v: bool(v))
                if sh.enabled() and valid(video_id) and not _SKIP.get():
                    ev = _asyncio.Event()
                    _INFLIGHT[video_id] = ev
                    t0 = _time.monotonic()
                    try:
                        raw = getattr(sh, "_raw_download", sh.download)
                        sp = await _asyncio.wait_for(
                            raw(video_id, tag, audio_only=audio_only, cancel_event=cancel_event),
                            timeout=_budget(),
                        )
                    except _asyncio.TimeoutError:
                        sp = None
                        _log("warning", "ShrutiAPI timeout %.0fs for %s", _budget(), video_id)
                    except _asyncio.CancelledError:
                        exc_cls = getattr(mod, "_DownloadCancelled", None)
                        if exc_cls is not None and cancel_event is not None and cancel_event.is_set():
                            raise exc_cls("shruti download superseded")
                        raise
                    finally:
                        ev.set()
                        _INFLIGHT.pop(video_id, None)
                    if sp:
                        if early_state is not None:
                            try:
                                if not early_state.ready.is_set():
                                    early_state.path = sp
                                    early_state.ready.set()
                            except Exception:  # noqa: BLE001
                                pass
                        return sp
                    _log("info", "ShrutiAPI miss for %s after %.1fs — yt-dlp/alt chain (4s cap)",
                         video_id, _time.monotonic() - t0)
                tok = _SKIP.set(True)
                try:
                    return await orig_impl(video_id, audio_only, tag, priority=priority,
                                           requested_at=requested_at, cancel_event=cancel_event,
                                           early_state=early_state)
                finally:
                    _SKIP.reset(tok)
            _download_audio_impl._shruti = True  # type: ignore[attr-defined]
            mod._download_audio_impl = _download_audio_impl

        orig_shield = getattr(mod, "_safe_shield", None)
        if orig_shield is not None and not getattr(orig_shield, "_shruti", False):
            async def _safe_shield(fut, video_id, timeout=15.0, audio_only=True):
                # Let the API finish before the hang-rescue clock starts.
                for _ in range(20):
                    ev = _INFLIGHT.get(video_id)
                    if ev is not None or fut.done():
                        break
                    await _asyncio.sleep(0.05)
                ev = _INFLIGHT.get(video_id)
                if ev is not None and not ev.is_set() and not fut.done():
                    w_fut = _asyncio.ensure_future(_asyncio.shield(fut))
                    w_ev = _asyncio.ensure_future(ev.wait())
                    try:
                        await _asyncio.wait({w_fut, w_ev}, timeout=_budget() + 2,
                                            return_when=_asyncio.FIRST_COMPLETED)
                    finally:
                        w_ev.cancel()
                        if not w_fut.done():
                            w_fut.cancel()
                    if fut.done():
                        return fut.result()
                return await orig_shield(fut, video_id, timeout=timeout, audio_only=audio_only)
            _safe_shield._shruti = True  # type: ignore[attr-defined]
            mod._safe_shield = _safe_shield
        _log("info", "⚡ ShrutiAPI fast path wired (API first, outside slot queue; yt-dlp keeps 4s cap)")

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
                    _log("warning", "ShrutiAPI status log failed: %r", exc)

        send_startup_log._shruti = True  # type: ignore[attr-defined]
        mod.send_startup_log = send_startup_log

    def _watch() -> None:
        done_y = done_m = False
        end = _time.time() + 600
        while _time.time() < end and not (done_y and done_m):
            y = _sys.modules.get("melody.core.ytdl")
            if (not done_y and y is not None and hasattr(y, "_download_audio_impl")
                    and hasattr(y, "_safe_shield") and hasattr(y, "should_try_direct_stream")):
                _patch_ytdl(y)
                done_y = True
            m = _sys.modules.get("__main__")
            if not done_m and m is not None and hasattr(m, "send_startup_log"):
                _patch_main(m)
                done_m = True
            _time.sleep(0.05)

    _th.Thread(target=_watch, name="shruti-patch", daemon=True).start()
