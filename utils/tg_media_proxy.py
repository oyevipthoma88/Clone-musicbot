"""Bounded localhost range proxy for large Telegram media.

PyTgCalls accepts HTTP media URLs, while Kurigram's ``stream_media`` yields
Telegram files in <=1 MiB chunks with an offset. This adapter bridges those two
APIs: FFmpeg can issue HEAD/Range requests and the proxy fetches only the
requested Telegram chunks. No full movie is kept in RAM or on disk.

BLUR / STALL ROOT CAUSE FIX
---------------------------
PyTgCalls starts TWO ffmpeg processes for a video stream (camera + microphone)
and each one opens its own HTTP range request against this proxy. The old
handler opened a *fresh* ``stream_media()`` session per request, so a single
1.8 GB movie was pulled from Telegram over 2-4 parallel file sessions, each
re-reading the very same bytes. Telegram throttles parallel reads of one file:
the log filled with ``resuming at byte 182422/1881194142 (TimeoutError)`` and
the video feed got so few bytes per second that NTgCalls could only publish a
heavily compressed, fully blurred picture.

Now every entry owns a small pool of Telegram sessions plus a shared chunk
cache. The second (audio) reader hits the cache instead of opening another
Telegram session, sequential reads keep re-using one warm iterator, and each
chunk is fetched from Telegram exactly once. That restores full throughput, so
the VC gets a sharp picture and continuous audio.
"""
from __future__ import annotations

import asyncio
import mimetypes
import os
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from urllib.parse import quote

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionError, ClientConnectionResetError

from melody.logging import LOGGER

_CHUNK_BYTES = 1024 * 1024
_MAX_PROXIES = 64


def _env_float(name: str, default: float, floor: float) -> float:
    try:
        return max(floor, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int, floor: int) -> int:
    try:
        return max(floor, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


_FIRST_CHUNK_TIMEOUT = _env_float("TG_PROXY_FIRST_CHUNK_TIMEOUT", 8.0, 2.0)
_CHUNK_TIMEOUT = _env_float("TG_PROXY_CHUNK_TIMEOUT", 25.0, 5.0)
_MAX_RESUME_ATTEMPTS = _env_int("TG_PROXY_RESUME_ATTEMPTS", 6, 1)
# How many chunks (1 MiB each) of one file stay in RAM so the audio reader can
# be served without touching Telegram again.
_CACHE_CHUNKS = _env_int("TG_PROXY_CACHE_CHUNKS", 48, 4)
# Telegram sessions allowed per file. 2 covers "video reader far ahead of the
# audio reader"; more only invites throttling.
_SESSIONS_PER_FILE = _env_int("TG_PROXY_FILE_SESSIONS", 2, 1)
_PROXY_TTL = 6 * 3600.0
_PROXY_CONCURRENCY = _env_int("TG_PROXY_CONCURRENCY", 12, 1)


@dataclass
class _Session:
    iterator: object | None = None
    next_index: int = -1
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class _MediaEntry:
    client: object
    message: object
    size: int
    content_type: str
    name: str
    touched: float
    cache: "OrderedDict[int, bytes]" = field(default_factory=OrderedDict)
    sessions: list = field(default_factory=list)

    def ensure_sessions(self) -> None:
        while len(self.sessions) < _SESSIONS_PER_FILE:
            self.sessions.append(_Session())


_entries: dict[str, _MediaEntry] = {}
_runner: web.AppRunner | None = None
_port: int | None = None
_server_lock = asyncio.Lock()
_stream_slots = asyncio.Semaphore(_PROXY_CONCURRENCY)


def _prune() -> None:
    now = time.monotonic()
    for token, entry in list(_entries.items()):
        if now - entry.touched > _PROXY_TTL:
            _entries.pop(token, None)
    if len(_entries) > _MAX_PROXIES:
        victims = sorted(_entries, key=lambda token: _entries[token].touched)
        for token in victims[: len(_entries) - _MAX_PROXIES]:
            _entries.pop(token, None)


def _content_type(name: str, fallback: str | None = None) -> str:
    return fallback or mimetypes.guess_type(name or "")[0] or "application/octet-stream"


def _parse_range(value: str | None, size: int) -> tuple[int, int] | None:
    if not value:
        return None
    if not value.startswith("bytes=") or "," in value:
        raise ValueError
    raw = value[6:].strip()
    if "-" not in raw:
        raise ValueError
    left, right = raw.split("-", 1)
    try:
        if left:
            start = int(left)
            end = int(right) if right else size - 1
        else:
            length = int(right)
            if length <= 0:
                raise ValueError
            start = max(0, size - length)
            end = size - 1
    except ValueError:
        raise ValueError from None
    if start < 0 or start >= size or end < start:
        raise ValueError
    return start, min(end, size - 1)


async def _close_iterator(iterator: object) -> None:
    aclose = getattr(iterator, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:  # pragma: no cover - cleanup must never raise
        pass


def _cache_put(entry: _MediaEntry, index: int, chunk: bytes) -> None:
    entry.cache[index] = chunk
    entry.cache.move_to_end(index)
    while len(entry.cache) > _CACHE_CHUNKS:
        entry.cache.popitem(last=False)


def _pick_session(entry: _MediaEntry, index: int) -> _Session:
    entry.ensure_sessions()
    # Prefer a warm, idle session already positioned at this chunk: continuing
    # an open iterator is what keeps Telegram throughput high.
    for session in entry.sessions:
        if session.next_index == index and not session.lock.locked():
            return session
    for session in entry.sessions:
        if not session.lock.locked():
            return session
    return min(entry.sessions, key=lambda s: abs(s.next_index - index))


async def _read_chunk(entry: _MediaEntry, index: int) -> bytes | None:
    """Return chunk ``index`` of the file, from cache or Telegram."""
    cached = entry.cache.get(index)
    if cached is not None:
        entry.cache.move_to_end(index)
        return cached

    session = _pick_session(entry, index)
    async with session.lock:
        cached = entry.cache.get(index)
        if cached is not None:
            entry.cache.move_to_end(index)
            return cached

        last_chunk = (entry.size - 1) // _CHUNK_BYTES
        attempts = 0
        while attempts <= _MAX_RESUME_ATTEMPTS:
            fresh = session.iterator is None or session.next_index != index
            if fresh:
                await _close_iterator(session.iterator)
                session.iterator = entry.client.stream_media(
                    entry.message,
                    limit=max(1, last_chunk - index + 1),
                    offset=index,
                ).__aiter__()
                session.next_index = index
            try:
                chunk = await asyncio.wait_for(
                    session.iterator.__anext__(),
                    timeout=_FIRST_CHUNK_TIMEOUT if fresh else _CHUNK_TIMEOUT,
                )
            except StopAsyncIteration:
                await _close_iterator(session.iterator)
                session.iterator = None
                session.next_index = -1
                return None
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except Exception as exc:
                await _close_iterator(session.iterator)
                session.iterator = None
                session.next_index = -1
                attempts += 1
                if attempts > _MAX_RESUME_ATTEMPTS:
                    LOGGER.warning(
                        "telegram media proxy gave up on chunk %s/%s after %s tries (%s)",
                        index, last_chunk, attempts, type(exc).__name__,
                    )
                    return None
                LOGGER.info(
                    "telegram media proxy retrying chunk %s/%s (%s, try %s)",
                    index, last_chunk, type(exc).__name__, attempts,
                )
                await asyncio.sleep(min(0.3 * attempts, 1.5))
                continue

            session.next_index = index + 1
            if not chunk:
                return None
            _cache_put(entry, index, chunk)
            return chunk
        return None


async def _media_handler(request: web.Request) -> web.StreamResponse:
    _prune()
    token = request.match_info.get("token", "")
    entry = _entries.get(token)
    if entry is None or entry.size <= 0:
        raise web.HTTPNotFound()
    entry.touched = time.monotonic()
    try:
        byte_range = _parse_range(request.headers.get("Range"), entry.size)
    except ValueError:
        raise web.HTTPRequestRangeNotSatisfiable(
            headers={"Content-Range": f"bytes */{entry.size}"}
        )

    if byte_range is None:
        start, end = 0, entry.size - 1
        status = 200
    else:
        start, end = byte_range
        status = 206
    length = end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Type": entry.content_type,
        "Cache-Control": "no-store",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{entry.size}"
    if request.method == "HEAD":
        return web.Response(status=status, headers=headers)

    async with _stream_slots:
        response = web.StreamResponse(status=status, headers=headers)
        try:
            await response.prepare(request)
        except (ClientConnectionResetError, ClientConnectionError, ConnectionResetError, BrokenPipeError):
            LOGGER.debug("telegram media proxy client disconnected before headers")
            return response
        remaining = length
        position = start
        try:
            while remaining > 0:
                index = position // _CHUNK_BYTES
                skip = position % _CHUNK_BYTES
                chunk = await _read_chunk(entry, index)
                if not chunk:
                    LOGGER.warning(
                        "telegram media proxy stopped at byte %s/%s (chunk %s unavailable)",
                        position, end, index,
                    )
                    break
                if skip:
                    chunk = chunk[skip:]
                    if not chunk:
                        break
                piece = chunk[:remaining]
                await response.write(piece)
                entry.touched = time.monotonic()
                remaining -= len(piece)
                position += len(piece)
            await response.write_eof()

        except asyncio.CancelledError:
            raise
        except (ClientConnectionResetError, ClientConnectionError, ConnectionResetError, BrokenPipeError):
            # FFmpeg may abandon a probe/range request as soon as it has enough
            # bytes. This is normal for a bounded proxy and must not produce a
            # traceback or take down the worker.
            LOGGER.debug("telegram media proxy client disconnected during stream")
            try:
                await response.write_eof()
            except Exception:
                pass
        except Exception as exc:  # Telegram disconnects must not crash the bot.
            LOGGER.debug("telegram media proxy request failed: %s", type(exc).__name__)
            try:
                await response.write_eof()
            except Exception:
                pass
        return response


async def _ensure_server() -> int:
    global _runner, _port
    if _runner is not None and _port is not None:
        return _port
    async with _server_lock:
        if _runner is not None and _port is not None:
            return _port
        app = web.Application(client_max_size=1)
        app.router.add_route("HEAD", "/tg/{token}/{name:.*}", _media_handler)
        app.router.add_route("GET", "/tg/{token}/{name:.*}", _media_handler)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        requested_port = int(os.getenv("TG_PROXY_PORT", "0") or 0)
        site = web.TCPSite(runner, "127.0.0.1", requested_port)
        await site.start()
        sockets = getattr(site, "_server", None).sockets if getattr(site, "_server", None) else None
        if not sockets:
            await runner.cleanup()
            raise RuntimeError("telegram media proxy failed to bind")
        _runner = runner
        _port = int(sockets[0].getsockname()[1])
        LOGGER.info("telegram media range proxy ready on localhost:%d", _port)
        return _port


async def create_media_proxy(client, message, *, size: int, filename: str = "media") -> str:
    """Register a Telegram media object and return a localhost HTTP URL."""
    size = int(size or 0)
    if size <= 0:
        raise ValueError("Telegram media size is unavailable")
    port = await _ensure_server()
    token = uuid.uuid4().hex
    name = os.path.basename(filename or "media")
    _entries[token] = _MediaEntry(
        client=client,
        message=message,
        size=size,
        content_type=_content_type(name, getattr(message, "mime_type", None)),
        name=name,
        touched=time.monotonic(),
    )
    _prune()
    return f"http://127.0.0.1:{port}/tg/{token}/{quote(name, safe='')}"


async def close_media_proxy_server() -> None:
    global _runner, _port
    async with _server_lock:
        if _runner is not None:
            await _runner.cleanup()
        _runner = None
        _port = None
        _entries.clear()
