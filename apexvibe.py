"""ApexVibe: a small Telegram voice-chat music bot.

Only two public commands are registered: /play and /skip.
The playback path is intentionally narrow:

1. YouTube Data API v3 or yt-dlp resolves one result.
2. A cached completed file is preferred.
3. Otherwise yt-dlp resolves a direct audio URL and PyTgCalls tries it first.
4. If the direct URL is rejected, a bounded, single-slot download is used.

No autoplay, startup recovery, GridFS, peer warm-up, social plugins, or large
background scans are included. Secrets are read only from environment variables.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from pyrogram import Client, filters
from pyrogram.types import Message
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream, StreamEnded

try:
    from pytgcalls.types import AudioQuality
except ImportError:  # pragma: no cover - compatibility with older builds
    AudioQuality = None


LOG = logging.getLogger("ApexVibe")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


# ── Environment ──────────────────────────────────────────────────────────────
def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


API_ID = _int_env("API_ID", 0)
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
STRING_SESSION = os.getenv("STRING_SESSION", "").strip()
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
YT_COOKIES = os.getenv("YT_COOKIES", "").strip()
COOKIE_URL = os.getenv("COOKIE_URL", "").strip()
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/tmp/apexvibe-cache"))
CACHE_LIMIT_MB = max(32, min(_int_env("CACHE_LIMIT_MB", 96), 256))
DIRECT_TIMEOUT = max(3.0, min(_float_env("DIRECT_TIMEOUT", 7.0), 20.0))
LOCAL_TIMEOUT = max(5.0, min(_float_env("LOCAL_TIMEOUT", 18.0), 45.0))
CONTROL_TIMEOUT = max(2.0, min(_float_env("CONTROL_TIMEOUT", 5.0), 15.0))
MAX_DOWNLOAD_SECONDS = max(30.0, min(_float_env("MAX_DOWNLOAD_SECONDS", 240.0), 900.0))


YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(slots=True)
class Track:
    video_id: str
    title: str
    webpage_url: str
    duration: int = 0
    source: str | None = None
    local_path: str | None = None


@dataclass
class ChatState:
    current: Track | None = None
    queue: deque[Track] = field(default_factory=deque)
    generation: int = 0
    play_task: asyncio.Task | None = None
    transition_task: asyncio.Task | None = None
    control_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stream_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    started_at: float = 0.0


states: dict[int, ChatState] = {}
_background_tasks: set[asyncio.Task] = set()
_download_lock = asyncio.Lock()
_http_client: httpx.AsyncClient | None = None
_cookie_file: Path | None = None


# ── Telegram clients ─────────────────────────────────────────────────────────
def _validate_config() -> None:
    missing = []
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not STRING_SESSION:
        missing.append("STRING_SESSION")
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))


bot: Client | None = None
assistant: Client | None = None
calls: PyTgCalls | None = None


def _state(chat_id: int) -> ChatState:
    return states.setdefault(chat_id, ChatState())


def _spawn(awaitable, name: str) -> asyncio.Task:
    task = asyncio.create_task(awaitable, name=name)
    _background_tasks.add(task)

    def done(finished: asyncio.Task) -> None:
        _background_tasks.discard(finished)
        if finished.cancelled():
            return
        try:
            error = finished.exception()
        except asyncio.CancelledError:
            return
        if error:
            LOG.error("background task %s failed: %s", name, error)

    task.add_done_callback(done)
    return task


# ── YouTube lookup and extraction ────────────────────────────────────────────
def _youtube_id(value: str) -> str | None:
    value = value.strip()
    if YOUTUBE_ID_RE.fullmatch(value):
        return value
    parsed = urlparse(value)
    host = parsed.netloc.lower().split(":", 1)[0]
    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/", 1)[0]
        return candidate if YOUTUBE_ID_RE.fullmatch(candidate) else None
    if host.endswith("youtube.com"):
        candidate = parse_qs(parsed.query).get("v", [""])[0]
        if YOUTUBE_ID_RE.fullmatch(candidate):
            return candidate
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
            return parts[1] if YOUTUBE_ID_RE.fullmatch(parts[1]) else None
    return None


def _yt_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _cookie_bytes(value: str) -> bytes:
    if "# Netscape HTTP Cookie File" in value or "\t" in value:
        return value.encode("utf-8")
    try:
        decoded = base64.b64decode(value, validate=True)
        if b"youtube.com" in decoded or b"Netscape" in decoded:
            return decoded
    except Exception:
        pass
    return value.encode("utf-8")


async def _prepare_cookies() -> Path | None:
    global _cookie_file
    if _cookie_file and _cookie_file.is_file():
        return _cookie_file
    raw = YT_COOKIES
    if not raw and COOKIE_URL:
        try:
            client = await _get_http_client()
            response = await client.get(COOKIE_URL, timeout=10)
            response.raise_for_status()
            raw = response.text
        except Exception as exc:  # noqa: BLE001
            LOG.warning("cookie URL unavailable; continuing without cookies: %s", exc)
    if not raw:
        return None
    path = Path("/tmp/apexvibe-cookies.txt")
    try:
        path.write_bytes(_cookie_bytes(raw))
        os.chmod(path, 0o600)
    except OSError as exc:
        LOG.warning("cookie file could not be prepared: %s", exc)
        return None
    _cookie_file = path
    return path


def _ydl_options(*, download: bool = False, output: str | None = None) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "ignoreerrors": False,
        "socket_timeout": 10,
        "retries": 2,
        "fragment_retries": 2,
        "concurrent_fragment_downloads": 1,
        "format": "bestaudio[ext=webm]/bestaudio[acodec=opus]/bestaudio/best",
        "extractor_args": {
            "youtube": {"player_client": ["android_music", "ios", "web"]}
        },
    }
    if download:
        options.update({
            "outtmpl": output,
            "overwrites": False,
            "continuedl": False,
            "nopart": True,
        })
    return options


def _extract_sync(url: str, *, download: bool = False, output: str | None = None):
    from yt_dlp import YoutubeDL

    options = _ydl_options(download=download, output=output)
    if _cookie_file and _cookie_file.is_file():
        options["cookiefile"] = str(_cookie_file)
    with YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=download)


async def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=4.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            headers={"User-Agent": "ApexVibe/1.0"},
        )
    return _http_client


async def _api_search(query: str) -> Track | None:
    if not YOUTUBE_API_KEY:
        return None
    client = await _get_http_client()
    response = await client.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part": "snippet",
            "q": query,
            "type": "video",
            "videoCategoryId": "10",
            "maxResults": 1,
            "key": YOUTUBE_API_KEY,
        },
    )
    response.raise_for_status()
    items = response.json().get("items") or []
    if not items:
        return None
    item = items[0]
    video_id = (item.get("id") or {}).get("videoId")
    snippet = item.get("snippet") or {}
    if not YOUTUBE_ID_RE.fullmatch(video_id or ""):
        return None
    return Track(video_id, snippet.get("title") or "YouTube audio", _yt_url(video_id))


async def find_track(query: str) -> Track | None:
    video_id = _youtube_id(query)
    if video_id:
        return Track(video_id, "YouTube track", _yt_url(video_id))
    try:
        track = await _api_search(query)
        if track:
            return track
    except Exception as exc:  # noqa: BLE001
        LOG.info("YouTube API search failed; using yt-dlp search: %s", exc)
    try:
        info = await asyncio.wait_for(
            asyncio.to_thread(_extract_sync, f"ytsearch1:{query}"), timeout=18
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("search failed: %s", exc)
        return None
    if not info:
        return None
    entry = (info.get("entries") or [info])[0]
    if not entry:
        return None
    video_id = entry.get("id")
    if not YOUTUBE_ID_RE.fullmatch(video_id or ""):
        return None
    return Track(
        video_id=video_id,
        title=(entry.get("title") or "YouTube track")[:180],
        webpage_url=entry.get("webpage_url") or _yt_url(video_id),
        duration=int(entry.get("duration") or 0),
    )


def _cache_files() -> list[Path]:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return [p for p in DOWNLOAD_DIR.glob("*.audio") if p.is_file()]


def _cache_path(video_id: str) -> Path:
    return DOWNLOAD_DIR / f"{video_id}.audio"


def _evict_cache() -> None:
    files = _cache_files()
    total = sum(path.stat().st_size for path in files)
    limit = CACHE_LIMIT_MB * 1024 * 1024
    for path in sorted(files, key=lambda item: item.stat().st_atime):
        if total <= limit:
            break
        try:
            size = path.stat().st_size
            path.unlink()
            total -= size
        except OSError:
            continue


def _cached_path(video_id: str) -> Path | None:
    path = _cache_path(video_id)
    if path.is_file() and path.stat().st_size > 0:
        path.touch()
        return path
    return None


async def resolve_direct(track: Track) -> Track | None:
    try:
        info = await asyncio.wait_for(
            asyncio.to_thread(_extract_sync, track.webpage_url), timeout=16
        )
    except Exception as exc:  # noqa: BLE001
        LOG.info("direct resolve failed: %s", exc)
        return None
    if not info:
        return None
    url = info.get("url")
    if not url:
        formats = info.get("formats") or []
        candidates = [item for item in formats if item.get("url") and item.get("acodec") not in {None, "none"}]
        if candidates:
            url = candidates[-1]["url"]
    if not url:
        return None
    track.title = (info.get("title") or track.title)[:180]
    track.duration = int(info.get("duration") or track.duration or 0)
    track.source = url
    return track


async def download_track(track: Track) -> Path | None:
    cached = _cached_path(track.video_id)
    if cached:
        return cached
    async with _download_lock:
        cached = _cached_path(track.video_id)
        if cached:
            return cached
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        target = _cache_path(track.video_id)
        staging_prefix = DOWNLOAD_DIR / f"{track.video_id}.download"
        for old in DOWNLOAD_DIR.glob(f"{track.video_id}.download.*"):
            with contextlib.suppress(OSError):
                old.unlink()
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    _extract_sync,
                    track.webpage_url,
                    download=True,
                    output=str(staging_prefix) + ".%(ext)s",
                ),
                timeout=MAX_DOWNLOAD_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            LOG.warning("bounded download failed: %s", exc)
            for old in DOWNLOAD_DIR.glob(f"{track.video_id}.download.*"):
                with contextlib.suppress(OSError):
                    old.unlink()
            return None
        candidates = [
            path for path in DOWNLOAD_DIR.glob(f"{track.video_id}.download.*")
            if path.is_file() and path.stat().st_size > 0
        ]
        if not candidates:
            return None
        try:
            os.replace(candidates[0], target)
        except OSError as exc:
            LOG.warning("completed download could not be committed: %s", exc)
            return None
        _evict_cache()
        return _cached_path(track.video_id)


# ── Playback ─────────────────────────────────────────────────────────────────
def _audio_quality():
    if AudioQuality is None:
        return None
    return getattr(AudioQuality, "HIGH_QUALITY", None) or getattr(AudioQuality, "STUDIO", None)


def _stream(source: str) -> MediaStream:
    kwargs = {"video_flags": MediaStream.Flags.IGNORE}
    quality = _audio_quality()
    if quality is not None:
        kwargs["audio_parameters"] = quality
    if source.startswith(("http://", "https://")):
        kwargs["ffmpeg_parameters"] = (
            "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        )
    return MediaStream(source, **kwargs)


def _is_current(chat_id: int, generation: int) -> bool:
    return _state(chat_id).generation == generation


async def _play_source(chat_id: int, source: str, timeout: float) -> bool:
    if calls is None:
        return False
    try:
        await asyncio.wait_for(calls.play(chat_id, _stream(source)), timeout=timeout)
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        LOG.info("PyTgCalls source handoff failed: %s", exc)
        return False


async def _play_track(chat_id: int, track: Track, generation: int) -> bool:
    state = _state(chat_id)
    if not _is_current(chat_id, generation):
        return False
    if track.local_path:
        source = track.local_path
        timeout = LOCAL_TIMEOUT
    else:
        direct = await resolve_direct(track)
        if not _is_current(chat_id, generation):
            return False
        source = direct.source if direct else None
        timeout = DIRECT_TIMEOUT
    if source and await _play_source(chat_id, source, timeout):
        if _is_current(chat_id, generation):
            state.current = track
            state.started_at = time.monotonic()
            return True
        return False
    if not _is_current(chat_id, generation):
        return False
    path = await download_track(track)
    if not path or not _is_current(chat_id, generation):
        return False
    track.local_path = str(path)
    if not await _play_source(chat_id, track.local_path, LOCAL_TIMEOUT):
        return False
    if _is_current(chat_id, generation):
        state.current = track
        state.started_at = time.monotonic()
        return True
    return False


async def _start_next(chat_id: int) -> None:
    state = _state(chat_id)
    async with state.control_lock:
        if state.play_task and not state.play_task.done():
            return
        if not state.queue:
            state.current = None
            return
        track = state.queue.popleft()
        state.generation += 1
        generation = state.generation
        state.current = track
        state.play_task = asyncio.create_task(
            _play_track(chat_id, track, generation), name=f"play-{chat_id}-{generation}"
        )
        task = state.play_task
    try:
        ok = await task
    except asyncio.CancelledError:
        return
    except Exception as exc:  # noqa: BLE001
        LOG.error("playback failed in chat %s: %s", chat_id, exc)
        ok = False
    async with state.control_lock:
        if state.generation != generation:
            return
        state.play_task = None
        if not ok:
            state.current = None
            state.started_at = 0.0
            if state.queue:
                _spawn(_start_next(chat_id), f"next-after-failure-{chat_id}")


async def _leave(chat_id: int) -> None:
    if calls is None:
        return
    try:
        await asyncio.wait_for(calls.leave_call(chat_id), timeout=CONTROL_TIMEOUT)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        LOG.debug("voice leave completed with %s", exc)


async def _skip(chat_id: int) -> None:
    state = _state(chat_id)
    async with state.control_lock:
        state.generation += 1
        old_task = state.play_task
        state.play_task = None
        state.current = None
        state.started_at = 0.0
        next_track = state.queue.popleft() if state.queue else None
        if old_task and not old_task.done():
            old_task.cancel()
    if old_task and not old_task.done():
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await old_task
    await _leave(chat_id)
    if next_track:
        async with state.control_lock:
            state.queue.appendleft(next_track)
        await _start_next(chat_id)


# ── Bot commands ─────────────────────────────────────────────────────────────
def register_handlers(client: Client) -> None:
    @client.on_message(filters.command("play") & filters.group)
    async def play_command(_, message: Message) -> None:
        query = " ".join(message.command[1:]).strip() if message.command else ""
        if not query:
            await message.reply_text("Usage: /play song name or YouTube link")
            return
        status = await message.reply_text("🔎 Searching…")
        track = await find_track(query)
        if not track:
            await status.edit_text("❌ Song nahi mila.")
            return
        state = _state(message.chat.id)
        async with state.control_lock:
            if state.current or (state.play_task and not state.play_task.done()):
                state.queue.append(track)
                queued = True
            else:
                state.generation += 1
                generation = state.generation
                state.current = track
                state.play_task = asyncio.create_task(
                    _play_track(message.chat.id, track, generation),
                    name=f"play-{message.chat.id}-{generation}",
                )
                queued = False
                task = state.play_task
        if queued:
            await status.edit_text(f"➕ Queue mein add: {track.title}")
            return
        await status.edit_text(f"▶️ Starting: {track.title}")
        try:
            ok = await task
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            LOG.error("play command failed: %s", exc)
            ok = False
        async with state.control_lock:
            if state.generation == generation:
                state.play_task = None
                if not ok:
                    state.current = None
                    await status.edit_text("❌ Playback start nahi ho paya.")

    @client.on_message(filters.command("skip") & filters.group)
    async def skip_command(_, message: Message) -> None:
        await message.reply_text("⏭ Skipping…")
        _spawn(_skip(message.chat.id), f"skip-{message.chat.id}")


async def on_stream_update(_, update) -> None:
    if not isinstance(update, StreamEnded):
        return
    chat_id = getattr(update, "chat_id", None)
    if chat_id is None:
        return
    state = _state(chat_id)
    async with state.control_lock:
        # The event type has no stream-generation id. A late end from the
        # previous source can arrive just after a replacement was accepted;
        # never treat an impossible early end as the new song finishing.
        if (
            state.current
            and state.started_at
            and time.monotonic() - state.started_at < 1.5
            and state.current.duration > 3
        ):
            LOG.debug("ignoring early stale StreamEnded in chat %s", chat_id)
            return
        # Ignore an end belonging to an already superseded operation.
        if state.play_task and not state.play_task.done():
            return
        state.current = None
        state.started_at = 0.0
        state.play_task = None
        if not state.queue:
            return
    # Reuse the same guarded starter as failure recovery. It takes the queue
    # lock only after the end event has released it, so /skip remains responsive.
    await _start_next(chat_id)


async def main() -> None:
    global bot, assistant, calls
    _validate_config()
    await _prepare_cookies()
    bot = Client("apexvibe-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    assistant = Client("apexvibe-assistant", api_id=API_ID, api_hash=API_HASH, session_string=STRING_SESSION)
    register_handlers(bot)
    await bot.start()
    await assistant.start()
    calls = PyTgCalls(assistant)
    @calls.on_update()
    async def _updates(client, update):
        try:
            await on_stream_update(client, update)
        except Exception as exc:  # noqa: BLE001
            LOG.error("voice update failed: %s", exc)
    await calls.start()
    LOG.info("ApexVibe started: music-only /play and /skip")
    try:
        await asyncio.Event().wait()
    finally:
        for task in list(_background_tasks):
            task.cancel()
        if _http_client and not _http_client.is_closed:
            await _http_client.aclose()
        await assistant.stop()
        await bot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
