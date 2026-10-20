"""ShrutiBots YouTube API source (https://shrutibots.site).

ROOT-CAUSE FIX: YouTube blocks Heroku IPs and burns cookies, so yt-dlp hits
the "Sign in to confirm you're not a bot" wall. This API downloads the
track on ITS servers and returns the raw audio/video file, so no cookies,
no PO-token and no Heroku IP ever touches YouTube for the media itself.

Env vars:
  SHRUTI_API_KEY          one key, or several comma-separated (auto-rotation on
                          quota / expiry / block / network error)
  SHRUTI_API_URL          optional, defaults to https://api.shrutibots.in
                          (falls back to https://shrutibots.site on network errors)
  SHRUTI_TIMEOUT          optional total seconds per attempt (default 60)
  SHRUTI_HEALTH_INTERVAL  seconds between "API ACTIVE?" checks (default 1800)

Endpoint used: GET /stream/{video_id}?type=audio|video&api_key=KEY
Response 200 = the file itself; errors = JSON {"detail": "..."}.
Key check uses GET /autoplay (does NOT spend daily quota).
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Optional

import httpx

from melody.logging import LOGGER

_CT_EXT = {
    "audio/webm": "webm",
    "audio/mp4": "m4a",
    "audio/m4a": "m4a",
    "audio/mpeg": "mp3",
    "video/mp4": "mp4",
    "video/x-matroska": "mkv",
    "video/webm": "webm",
}

# key -> unix time until which it must not be used
_key_cooldown: dict[str, float] = {}
_client: Optional[httpx.AsyncClient] = None

# Live status - shown in startup log + periodic "API active?" log lines.
STATUS: dict = {
    "checked_at": 0.0, "api_up": None, "valid_keys": 0, "total_keys": 0,
    "detail": "not checked yet", "ok": 0, "fail": 0, "last_ok": 0.0,
    "last_error": "",
}
_monitor_task: "asyncio.Task | None" = None


def _keys() -> list[str]:
    raw = os.environ.get("SHRUTI_API_KEY", "") or os.environ.get("SHRUTI_API_KEYS", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def enabled() -> bool:
    return bool(_keys())


_DEFAULT_BASES = ("https://api.shrutibots.in", "https://shrutibots.site")


def _bases() -> list[str]:
    """Official base first (per /docs), legacy domain as network fallback."""
    custom = (os.environ.get("SHRUTI_API_URL") or "").strip().rstrip("/")
    out = [custom] if custom else []
    out += [b for b in _DEFAULT_BASES if b not in out]
    return out


def _base() -> str:
    return _bases()[0]


def _timeout() -> float:
    try:
        return float(os.environ.get("SHRUTI_TIMEOUT", "60"))
    except ValueError:
        return 60.0


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(_timeout(), connect=8.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


_last_revalidate = 0.0


async def _revalidate_keys() -> list[str]:
    """All keys paused: re-check them via /autoplay (spends NO daily quota)
    at most once a minute, so a transient 401/403 or a key you just renewed
    on @SHRUTIAPIBOT is picked up without restarting the dyno."""
    global _last_revalidate
    now = time.time()
    if now - _last_revalidate < 60:
        return []
    _last_revalidate = now
    client = _get_client()
    good = []
    for key in _keys():
        try:
            r = await client.get(f"{_base()}/autoplay",
                                 params={"video_id": "dQw4w9WgXcQ", "api_key": key}, timeout=8.0)
            if r.status_code == 200:
                _key_cooldown.pop(key, None)
                good.append(key)
            else:
                STATUS["last_error"] = f"key ...{key[-4:]}: {r.status_code} {r.text[:120]}"
                LOGGER.error("🔴 ShrutiAPI key ...%s still rejected: %s %s",
                             key[-4:], r.status_code, r.text[:120])
        except Exception as exc:  # noqa: BLE001
            STATUS["last_error"] = repr(exc)[:200]
    if good:
        LOGGER.info("🟢 ShrutiAPI: %d key(s) usable again", len(good))
    return good


def _live_keys() -> list[str]:
    now = time.time()
    return [k for k in _keys() if _key_cooldown.get(k, 0) <= now]


def _cool(key: str, seconds: float, why: str) -> None:
    _key_cooldown[key] = time.time() + seconds
    STATUS["last_error"] = f"key ...{key[-4:]}: {why}"[:200]
    LOGGER.warning("ShrutiAPI key ...%s paused %ss: %s", key[-4:], int(seconds), why)
    if not _live_keys():
        LOGGER.error("🔴 ShrutiAPI INACTIVE - all keys paused/expired. Get a new key from @SHRUTIAPIBOT")


async def health_check() -> dict:
    """Check /health (no key) + validate every key via /autoplay, which does
    NOT spend daily quota. Logs a clear ACTIVE / INACTIVE line."""
    keys = _keys()
    STATUS.update(total_keys=len(keys), checked_at=time.time())
    if not keys:
        STATUS.update(api_up=None, valid_keys=0, detail="SHRUTI_API_KEY not set")
        LOGGER.warning("⚪ ShrutiAPI DISABLED - SHRUTI_API_KEY env var is empty (YouTube via Heroku IP only)")
        return STATUS
    client = _get_client()
    try:
        r = await client.get(f"{_base()}/health", timeout=10.0)
        up = r.status_code == 200 and "healthy" in r.text
    except Exception as exc:  # noqa: BLE001
        up = False
        STATUS["detail"] = f"health error: {exc!r}"[:200]
    STATUS["api_up"] = up
    valid = 0
    reasons = []
    for key in keys:
        try:
            kr = await client.get(f"{_base()}/autoplay",
                                  params={"video_id": "dQw4w9WgXcQ", "api_key": key},
                                  timeout=15.0)
            if kr.status_code == 200:
                valid += 1
                _key_cooldown.pop(key, None)
            else:
                d = kr.text[:120]
                reasons.append(f"...{key[-4:]}={kr.status_code} {d}")
                if kr.status_code in (401, 403) and "IP" not in d:
                    _key_cooldown[key] = time.time() + 1800
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"...{key[-4:]}={exc!r}"[:120])
    STATUS["valid_keys"] = valid
    if up and valid:
        STATUS["detail"] = f"{valid}/{len(keys)} key(s) valid"
        LOGGER.info("🟢 ShrutiAPI ACTIVE - server healthy, %d/%d key(s) valid. /play + /vplay use API (no YouTube cookies/IP needed)", valid, len(keys))
    else:
        STATUS["detail"] = ("server down" if not up else "no valid key") + (
            ": " + "; ".join(reasons) if reasons else "")
        LOGGER.error("🔴 ShrutiAPI INACTIVE - %s. Falling back to yt-dlp/JioSaavn", STATUS["detail"])
    return STATUS


def is_active() -> bool:
    return bool(STATUS.get("api_up")) and bool(_live_keys()) and STATUS.get("valid_keys", 0) > 0


def status_html() -> str:
    if not _keys():
        return "⚪ <b>YT API:</b> disabled (SHRUTI_API_KEY not set)"
    icon = "🟢 ACTIVE" if is_active() else "🔴 INACTIVE"
    return (f"🎧 <b>YT API (ShrutiBots):</b> {icon}\n"
            f"   └ {STATUS['detail']} · served {STATUS['ok']} ok / {STATUS['fail']} fail")


async def send_status(bot, assistant) -> None:
    """Post the API status to LOG_GROUP_ID (assistant first, then bot)."""
    try:
        from melody.config import Config
        chat = getattr(Config, "LOG_GROUP_ID", None) or getattr(Config, "OWNER_ID", None)
    except Exception:  # noqa: BLE001
        chat = None
    if not chat:
        return
    text = status_html()
    if STATUS.get("last_error") and not is_active():
        text += f"\n   └ last error: <code>{STATUS['last_error'][:150]}</code>"
    for c in (assistant, bot):
        if c is None:
            continue
        try:
            await c.send_message(chat, text)
            return
        except Exception:  # noqa: BLE001
            continue


def start_monitor() -> None:
    """Re-check every SHRUTI_HEALTH_INTERVAL seconds (default 1800) and log."""
    global _monitor_task
    if _monitor_task is not None and not _monitor_task.done():
        return
    try:
        every = max(300.0, float(os.environ.get("SHRUTI_HEALTH_INTERVAL", "1800")))
    except ValueError:
        every = 1800.0

    async def _loop():
        while True:
            await asyncio.sleep(every)
            try:
                await health_check()
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("ShrutiAPI monitor error: %r", exc)

    _monitor_task = asyncio.get_running_loop().create_task(_loop())


def _fail(why: str) -> None:
    STATUS["fail"] += 1
    STATUS["last_error"] = why[:200]


async def _download_file(
    video_id: str, tag: str, audio_only: bool = True,
) -> Optional[str]:
    """Download the full file into /tmp/melody_<video_id>_<tag>.<ext>.

    Rotates keys on key errors, retries 429/500/network errors briefly and
    falls back to the legacy domain on network errors. Every failure is
    logged with the API's own reason, so "gana nahi baja" is never a mystery.
    """
    keys = _live_keys()
    if not keys:
        keys = await _revalidate_keys()
    if not keys:
        _fail("no usable SHRUTI_API_KEY (all keys paused/expired) — "
              + str(STATUS.get("last_error") or "check key in @SHRUTIAPIBOT"))
        LOGGER.error("🔴 ShrutiAPI: no usable key — get a new one from @SHRUTIAPIBOT")
        return None
    kind = "audio" if audio_only else "video"
    client = _get_client()
    t0 = time.monotonic()
    tmp = f"/tmp/melody_{video_id}_{tag}.shruti.part"

    for key in keys:
        for base in _bases():
            url = f"{base}/stream/{video_id}"
            for attempt in range(2):
                try:
                    async with client.stream("GET", url, params={"type": kind, "api_key": key}) as r:
                        if r.status_code != 200:
                            body = (await r.aread())[:300].decode("utf-8", "ignore")
                            s = r.status_code
                            LOGGER.warning("ShrutiAPI %s %s -> HTTP %s: %s", kind, video_id, s, body)
                            low = body.lower()
                            if s == 401 or (s == 403 and "your ip" not in low):
                                _cool(key, 1800, f"{s} {body}")
                                break  # next key
                            if s == 429 and "quota" in low:
                                _cool(key, 1800, body)
                                break  # next key
                            if s == 403:  # IP blocked: other keys/domains won't help now
                                _fail(f"{s} {body}")
                                return None
                            if s in (429, 500, 502, 503, 504) and attempt == 0:
                                await asyncio.sleep(1.0)
                                continue
                            _fail(f"{s} {body}")
                            return None  # 400 / repeated 500: video-side problem
                        ct = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
                        if ct.startswith("application/json") or ct.startswith("text/"):
                            body = (await r.aread())[:300].decode("utf-8", "ignore")
                            LOGGER.warning("ShrutiAPI non-media reply for %s (%s): %s", video_id, ct, body)
                            _fail(f"non-media {ct}: {body}")
                            return None
                        ext = _CT_EXT.get(ct) or ("m4a" if audio_only else "mp4")
                        size = 0
                        with open(tmp, "wb") as fh:
                            async for chunk in r.aiter_bytes(256 * 1024):
                                fh.write(chunk)
                                size += len(chunk)
                    if size < 10 * 1024:
                        LOGGER.warning("ShrutiAPI tiny file (%sB) for %s", size, video_id)
                        _rm(tmp)
                        _fail(f"tiny file {size}B")
                        return None
                    final = f"/tmp/melody_{video_id}_{tag}.{ext}"
                    os.replace(tmp, final)
                    STATUS["ok"] += 1
                    STATUS["last_ok"] = time.time()
                    LOGGER.info("⚡ ShrutiAPI %s %s ready in %.1fs (%d KB) via %s",
                                kind, video_id, time.monotonic() - t0, size // 1024, base)
                    return final
                except asyncio.CancelledError:
                    _rm(tmp)
                    raise
                except Exception as exc:  # noqa: BLE001  network / timeout
                    _rm(tmp)
                    LOGGER.warning("ShrutiAPI network error for %s via %s: %r", video_id, base, exc)
                    STATUS["last_error"] = repr(exc)[:200]
                    break  # try the next domain
            else:
                continue
            # inner loop broke: key error -> next key, network error -> next base
            if key in _key_cooldown and _key_cooldown[key] > time.time():
                break
    _fail(STATUS.get("last_error") or "all keys/domains failed")
    return None


def _rm(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


_inflight: dict = {}


def _cached_file(video_id: str, tag: str) -> Optional[str]:
    import glob
    for f in glob.glob(f"/tmp/melody_{video_id}_{tag}.*"):
        if f.endswith(".part"):
            continue
        try:
            if os.path.getsize(f) >= 10 * 1024:
                return f
        except OSError:
            pass
    return None


async def download(
    video_id: str, tag: str, audio_only: bool = True,
    cancel_event: "threading.Event | None" = None,
    allow_early: bool = True,  # kept for API compatibility; ignored
) -> Optional[str]:
    """Return a COMPLETE local file for video_id (or None on failure).

    ROOT FIX: the /stream API URL is not a live stream (the API fetches the
    track first, then sends it), so handing it to FFmpeg made the VC silent.
    A finished local file always plays, seeks and resumes. Concurrent callers
    (play + prefetch + resume) share one download -> quota is spent once.
    """
    cached = _cached_file(video_id, tag)
    if cached:
        return cached
    key = f"{video_id}:{tag}:{'a' if audio_only else 'v'}"
    task = _inflight.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(_download_file(video_id, tag, audio_only=audio_only))
        _inflight[key] = task
        task.add_done_callback(lambda _t, k=key: _inflight.pop(k, None))
    while not task.done():
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError()  # shared download keeps running
        await asyncio.wait({asyncio.shield(task)}, timeout=0.25)
    if task.cancelled():
        return None
    exc = task.exception()
    if exc is not None:
        LOGGER.warning("ShrutiAPI download failed for %s: %r", video_id, exc)
        return None
    return task.result()


def last_error() -> str:
    return str(STATUS.get("last_error") or "unknown error")
