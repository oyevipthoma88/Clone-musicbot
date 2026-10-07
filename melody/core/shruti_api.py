"""ShrutiBots YouTube API source (https://shrutibots.site).

ROOT-CAUSE FIX: YouTube blocks Heroku IPs and burns cookies, so yt-dlp hits
the "Sign in to confirm you're not a bot" wall. This API downloads the
track on ITS servers and returns the raw audio/video file, so no cookies,
no PO-token and no Heroku IP ever touches YouTube for the media itself.

Env vars:
  SHRUTI_API_KEY   one key, or several comma-separated (auto-rotation on
                   quota / expiry / block)
  SHRUTI_API_URL   optional, defaults to https://shrutibots.site
  SHRUTI_TIMEOUT   optional total seconds per attempt (default 60)

Endpoint used: GET /stream/{video_id}?type=audio|video&api_key=KEY
Response 200 = the file itself; errors = JSON {"detail": "..."}.
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


def _keys() -> list[str]:
    raw = os.environ.get("SHRUTI_API_KEY", "") or os.environ.get("SHRUTI_API_KEYS", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def enabled() -> bool:
    return bool(_keys())


def _base() -> str:
    return (os.environ.get("SHRUTI_API_URL") or "https://shrutibots.site").rstrip("/")


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


def _live_keys() -> list[str]:
    now = time.time()
    return [k for k in _keys() if _key_cooldown.get(k, 0) <= now]


def _cool(key: str, seconds: float, why: str) -> None:
    _key_cooldown[key] = time.time() + seconds
    LOGGER.warning("ShrutiAPI key ...%s paused %ss: %s", key[-4:], int(seconds), why)


async def download(
    video_id: str, tag: str, audio_only: bool = True,
    cancel_event: "threading.Event | None" = None,
) -> Optional[str]:
    """Download into /tmp/melody_<video_id>_<tag>.<ext>; return path or None."""
    keys = _live_keys()
    if not keys:
        return None
    kind = "audio" if audio_only else "video"
    url = f"{_base()}/stream/{video_id}"
    client = _get_client()
    t0 = time.monotonic()

    for key in keys:
        tmp = f"/tmp/melody_{video_id}_{tag}.shruti.part"
        try:
            async with client.stream("GET", url, params={"type": kind, "api_key": key}) as r:
                if r.status_code != 200:
                    body = (await r.aread())[:300].decode("utf-8", "ignore")
                    s = r.status_code
                    if s in (401, 403) and "IP" not in body:
                        _cool(key, 6 * 3600, f"{s} {body}")
                        continue
                    if s == 429 and "quota" in body.lower():
                        _cool(key, 3 * 3600, body)
                        continue
                    if s == 429:
                        await asyncio.sleep(1.5)
                        continue
                    LOGGER.info("ShrutiAPI %s for %s: %s", s, video_id, body)
                    return None  # 400/500: video-side problem, other keys won't help
                ct = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
                if ct.startswith("application/json") or ct.startswith("text/"):
                    LOGGER.info("ShrutiAPI non-media reply for %s: %s", video_id, ct)
                    return None
                ext = _CT_EXT.get(ct) or ("m4a" if audio_only else "mp4")
                size = 0
                with open(tmp, "wb") as fh:
                    async for chunk in r.aiter_bytes(256 * 1024):
                        if cancel_event is not None and cancel_event.is_set():
                            raise asyncio.CancelledError()
                        fh.write(chunk)
                        size += len(chunk)
            if size < 10 * 1024:
                LOGGER.info("ShrutiAPI tiny file (%sB) for %s", size, video_id)
                _rm(tmp)
                return None
            final = f"/tmp/melody_{video_id}_{tag}.{ext}"
            os.replace(tmp, final)
            LOGGER.info("⚡ ShrutiAPI %s %s ready in %.1fs (%d KB)",
                        kind, video_id, time.monotonic() - t0, size // 1024)
            return final
        except asyncio.CancelledError:
            _rm(tmp)
            raise
        except Exception as exc:  # noqa: BLE001
            _rm(tmp)
            LOGGER.warning("ShrutiAPI error for %s: %r", video_id, exc)
            return None
    return None


def _rm(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
