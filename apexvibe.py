"""ApexVibe: a small Telegram voice-chat music bot.

The public command set covers playback, queue, and essential music controls; no unrelated social or administration plugins are registered.
The playback path is intentionally narrow:

1. YouTube Data API v3 or yt-dlp resolves one result.
2. A cached completed file is preferred.
3. Otherwise yt-dlp resolves a direct audio URL and PyTgCalls tries it first.
4. If the direct URL is rejected, a bounded, single-slot download is used.

No autoplay, startup recovery, GridFS, peer warm-up, social plugins, or large
background scans are included. Secrets are read only from environment variables.

Music commands include /play, /vplay, /cplay, /playforce, /skip, /pause,
/resume, /stop, /queue, /now, /clearqueue, /remove, /shuffle, /loop,
/volume, /seek, /speed, /search, /playlist, /song, and /help.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import html
import io
import logging
import os
import random
import re
import sys
import textwrap
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from pyrogram import Client, enums, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
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


def _flag_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


API_ID = _int_env("API_ID", 0)
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
STRING_SESSION = os.getenv("STRING_SESSION", "").strip()
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
YT_COOKIES = os.getenv("YT_COOKIES", "").strip()
LOG_GROUP_ID = _int_env("LOG_GROUP_ID", 0)
OWNER_ID = _int_env("OWNER_ID", 0)
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "").strip()
UPDATE_CHANNEL = os.getenv("UPDATE_CHANNEL", "").strip()
SUPPORT_GROUP = os.getenv("SUPPORT_GROUP", "").strip()
SUPPORT_CHANNEL = os.getenv("SUPPORT_CHANNEL", "").strip()
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/tmp/apexvibe-cache"))
CACHE_LIMIT_MB = max(32, min(_int_env("CACHE_LIMIT_MB", 96), 256))
DIRECT_TIMEOUT = max(3.0, min(_float_env("DIRECT_TIMEOUT", 7.0), 20.0))
LOCAL_TIMEOUT = max(5.0, min(_float_env("LOCAL_TIMEOUT", 18.0), 45.0))
CONTROL_TIMEOUT = max(2.0, min(_float_env("CONTROL_TIMEOUT", 5.0), 15.0))
MAX_DOWNLOAD_SECONDS = max(30.0, min(_float_env("MAX_DOWNLOAD_SECONDS", 240.0), 900.0))
AUTOPLAY_ENABLED = _flag_env("AUTOPLAY", True)
AUTOPLAY_TIMEOUT = max(8.0, min(_float_env("AUTOPLAY_TIMEOUT", 25.0), 60.0))
CLONE_MODE = _flag_env("CLONE_MODE", False)
MAX_ACTIVE_CLONES = max(1, min(_int_env("MAX_ACTIVE_CLONES", 2), 4))
SESSION_DIR = Path(os.getenv("APEXVIBE_SESSION_DIR", "/tmp/apexvibe-session"))


YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(slots=True)
class Track:
    video_id: str
    title: str
    webpage_url: str
    duration: int = 0
    source: str | None = None
    local_path: str | None = None
    uploader: str = "YouTube"
    thumbnail: str | None = None


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
    loop_mode: str = "off"
    volume: int = 100
    speed: float = 1.0
    paused: bool = False
    autoplaying: bool = False
    autoplay_enabled: bool | None = None


states: dict[int, ChatState] = {}
_background_tasks: set[asyncio.Task] = set()
_download_lock = asyncio.Lock()
_http_client: httpx.AsyncClient | None = None
_cookie_file: Path | None = None
_setup_sessions: dict[int, dict] = {}
_clone_processes: dict[int, asyncio.subprocess.Process] = {}


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
    if raw.startswith(("https://", "http://")):
        try:
            client = await _get_http_client()
            response = await client.get(raw, timeout=10)
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
    thumbs = snippet.get("thumbnails") or {}
    thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url")
    return Track(
        video_id, snippet.get("title") or "YouTube audio", _yt_url(video_id),
        uploader=snippet.get("channelTitle") or "YouTube", thumbnail=thumb,
    )


def _track_from_entry(entry: dict) -> Track | None:
    video_id = entry.get("id")
    if not YOUTUBE_ID_RE.fullmatch(video_id or ""):
        return None
    return Track(
        video_id=video_id,
        title=(entry.get("title") or "YouTube track")[:180],
        webpage_url=entry.get("webpage_url") or _yt_url(video_id),
        duration=int(entry.get("duration") or 0),
        uploader=entry.get("uploader") or entry.get("channel") or "YouTube",
        thumbnail=entry.get("thumbnail"),
    )


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
    return _track_from_entry(entry)


def _playlist_sync(url: str) -> dict:
    from yt_dlp import YoutubeDL

    options = _ydl_options()
    options["extract_flat"] = "in_playlist"
    options["playlistend"] = 20
    if _cookie_file and _cookie_file.is_file():
        options["cookiefile"] = str(_cookie_file)
    with YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


async def find_tracks(query: str, limit: int = 10) -> list[Track]:
    """Resolve a playlist URL or return a single search result."""
    parsed = urlparse(query)
    has_playlist = bool(parse_qs(parsed.query).get("list"))
    if not has_playlist:
        one = await find_track(query)
        return [one] if one else []
    try:
        info = await asyncio.wait_for(
            asyncio.to_thread(_playlist_sync, query), timeout=25
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("playlist resolve failed: %s", exc)
        return []
    tracks = []
    for entry in (info.get("entries") or [])[:limit]:
        track = _track_from_entry(entry or {})
        if track:
            tracks.append(track)
    return tracks


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
    track.uploader = (info.get("uploader") or info.get("channel") or track.uploader)[:120]
    track.thumbnail = info.get("thumbnail") or track.thumbnail
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


def _stream(source: str, *, seek: int = 0, speed: float = 1.0) -> MediaStream:
    kwargs = {"video_flags": MediaStream.Flags.IGNORE}
    quality = _audio_quality()
    if quality is not None:
        kwargs["audio_parameters"] = quality
    params = []
    if source.startswith(("http://", "https://")):
        params.extend(["-reconnect 1", "-reconnect_streamed 1", "-reconnect_delay_max 5"])
    if seek > 0:
        params.append(f"-ss {int(seek)}")
    if abs(speed - 1.0) > 0.01:
        params.append(f"-filter:a atempo={max(0.5, min(speed, 2.0)):.2f}")
    if params:
        kwargs["ffmpeg_parameters"] = " ".join(params)
    return MediaStream(source, **kwargs)


def _is_current(chat_id: int, generation: int) -> bool:
    return _state(chat_id).generation == generation


async def _play_source(chat_id: int, source: str, timeout: float, *, seek: int = 0, speed: float = 1.0) -> bool:
    if calls is None:
        return False
    try:
        await asyncio.wait_for(calls.play(chat_id, _stream(source, seek=seek, speed=speed)), timeout=timeout)
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        LOG.info("PyTgCalls source handoff failed: %s", exc)
        return False


async def _play_track(chat_id: int, track: Track, generation: int, *, seek: int = 0) -> bool:
    state = _state(chat_id)
    speed = state.speed
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
    if source and await _play_source(chat_id, source, timeout, seek=seek, speed=speed):
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
    if not await _play_source(chat_id, track.local_path, LOCAL_TIMEOUT, seek=seek, speed=speed):
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
        transition = state.transition_task
        state.play_task = None
        state.transition_task = None
        state.current = None
        state.started_at = 0.0
        next_track = state.queue.popleft() if state.queue else None
        for task in (old_task, transition):
            if task and not task.done() and task is not asyncio.current_task():
                task.cancel()
    for task in (old_task, transition):
        if task and not task.done() and task is not asyncio.current_task():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    await _leave(chat_id)
    if next_track:
        async with state.control_lock:
            state.queue.appendleft(next_track)
        await _start_next(chat_id)


# ── Autoplay ───────────────────────────────────────────────────────────────────
async def _find_related_track(previous: Track) -> Track | None:
    """Find one related audio result without a second heavy player pipeline."""
    query = f"{previous.title} official audio"
    try:
        info = await asyncio.wait_for(
            asyncio.to_thread(_extract_sync, f"ytsearch5:{query}"),
            timeout=AUTOPLAY_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        LOG.info("autoplay lookup failed: %s", exc)
        return None
    entries = info.get("entries") or []
    for entry in entries:
        candidate = _track_from_entry(entry or {})
        if candidate and candidate.video_id != previous.video_id:
            return candidate
    return None


async def _autoplay_next(chat_id: int, previous: Track, token: int) -> None:
    state = _state(chat_id)
    try:
        candidate = await _find_related_track(previous)
        async with state.control_lock:
            if not AUTOPLAY_ENABLED or state.generation != token or state.current is not None:
                return
            if not candidate:
                return
            state.generation += 1
            generation = state.generation
            state.current = candidate
            state.autoplaying = True
            state.play_task = asyncio.create_task(
                _play_track(chat_id, candidate, generation),
                name=f"autoplay-{chat_id}-{generation}",
            )
            play_task = state.play_task
        try:
            ok = await play_task
        except asyncio.CancelledError:
            return
        finally:
            async with state.control_lock:
                if state.play_task is play_task:
                    state.play_task = None
                state.autoplaying = False
        if not ok:
            async with state.control_lock:
                if state.generation == generation:
                    state.current = None
    finally:
        async with state.control_lock:
            if state.transition_task is asyncio.current_task():
                state.transition_task = None
            state.autoplaying = False


# ── Music controls ───────────────────────────────────────────────────────────
def _format_time(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _parse_time(value: str) -> int | None:
    value = value.strip().lower()
    if not value:
        return None
    try:
        if ":" in value:
            parts = [int(part) for part in value.split(":")]
            if len(parts) == 2:
                return max(0, parts[0] * 60 + parts[1])
            if len(parts) == 3:
                return max(0, parts[0] * 3600 + parts[1] * 60 + parts[2])
        match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", value)
        if match and any(match.groups()):
            hours, minutes, seconds = (int(item or 0) for item in match.groups())
            return hours * 3600 + minutes * 60 + seconds
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


async def _control(method: str, chat_id: int, *args) -> bool:
    if calls is None:
        return False
    function = getattr(calls, method, None)
    if function is None:
        return False
    try:
        result = await asyncio.wait_for(function(chat_id, *args), timeout=CONTROL_TIMEOUT)
        return result is not False
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        LOG.info("control %s failed in %s: %s", method, chat_id, exc)
        return False


async def _takeover_autoplay(chat_id: int) -> None:
    state = _state(chat_id)
    async with state.control_lock:
        if not state.autoplaying:
            return
        state.generation += 1
        old_task = state.play_task
        transition = state.transition_task
        had_current = state.current is not None
        state.play_task = None
        state.transition_task = None
        state.current = None
        state.started_at = 0.0
        state.autoplaying = False
        for task in (old_task, transition):
            if task and not task.done() and task is not asyncio.current_task():
                task.cancel()
    for task in (old_task, transition):
        if task and not task.done() and task is not asyncio.current_task():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    if had_current:
        await _leave(chat_id)


async def _stop_current_only(chat_id: int) -> None:
    state = _state(chat_id)
    async with state.control_lock:
        state.generation += 1
        old_task = state.play_task
        transition = state.transition_task
        state.play_task = None
        state.transition_task = None
        state.current = None
        state.started_at = 0.0
        for task in (old_task, transition):
            if task and not task.done() and task is not asyncio.current_task():
                task.cancel()
    for task in (old_task, transition):
        if task and not task.done() and task is not asyncio.current_task():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    await _leave(chat_id)


async def _restart_current(chat_id: int, *, seek: int | None = None) -> bool:
    state = _state(chat_id)
    async with state.control_lock:
        track = state.current
        if not track:
            return False
        state.generation += 1
        generation = state.generation
        old_task = state.play_task
        if old_task and not old_task.done():
            old_task.cancel()
        state.play_task = asyncio.create_task(
            _play_track(chat_id, track, generation, seek=max(0, seek or 0)),
            name=f"restart-{chat_id}-{generation}",
        )
        task = state.play_task
    if old_task and not old_task.done():
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await old_task
    try:
        result = await task
    except asyncio.CancelledError:
        return False
    finally:
        async with state.control_lock:
            if state.play_task is task:
                state.play_task = None
    return bool(result)


async def _edit(message: Message, text: str) -> None:
    with contextlib.suppress(Exception):
        await message.edit_text(text)


async def _make_thumbnail(track: Track) -> Path | None:
    """Create a small cached card image; image failure never blocks playback."""
    if not track.thumbnail:
        return None
    target = DOWNLOAD_DIR / f"{track.video_id}.card.jpg"
    if target.is_file() and target.stat().st_size > 0:
        return target
    try:
        client = await _get_http_client()
        async with client.stream("GET", track.thumbnail, timeout=8) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes(64 * 1024):
                data.extend(chunk)
                if len(data) > 4 * 1024 * 1024:
                    return None
        from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
        cover = Image.open(io.BytesIO(data)).convert("RGB")
        cover.thumbnail((520, 520))
        background = ImageOps.fit(cover, (1280, 720)).filter(ImageFilter.GaussianBlur(22))
        canvas = background.copy().convert("RGB")
        overlay = Image.new("RGBA", canvas.size, (8, 13, 28, 155))
        canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay)
        draw = ImageDraw.Draw(canvas)
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        regular_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        title_font = ImageFont.truetype(font_path, 48) if Path(font_path).exists() else ImageFont.load_default()
        meta_font = ImageFont.truetype(regular_path, 28) if Path(regular_path).exists() else ImageFont.load_default()
        cover = ImageOps.fit(cover, (420, 420))
        canvas.paste(cover, (80, 150))
        title = "\n".join(textwrap.wrap(track.title, width=28)[:2])
        draw.text((560, 170), title, font=title_font, fill=(255, 255, 255, 255), spacing=12)
        draw.text((560, 330), f"{track.uploader[:50]}  •  {_format_time(track.duration)}", font=meta_font, fill=(212, 226, 242, 255))
        draw.text((560, 590), "APEXVIBE  •  NOW PLAYING", font=meta_font, fill=(255, 190, 70, 255))
        canvas.convert("RGB").save(target, "JPEG", quality=88, optimize=True)
        return target
    except Exception as exc:  # noqa: BLE001
        LOG.debug("thumbnail generation skipped: %s", exc)
        return None


def _play_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏸ Pause", callback_data="av:pause"), InlineKeyboardButton("▶️ Resume", callback_data="av:resume")],
        [InlineKeyboardButton("⏭ Skip", callback_data="av:skip"), InlineKeyboardButton("📋 Queue", callback_data="av:queue")],
        [InlineKeyboardButton("🔊 Mute", callback_data="av:mute"), InlineKeyboardButton("✖ Close", callback_data="av:close")],
    ])


def _mask(value: str, keep: int = 4) -> str:
    value = value or ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}…{value[-keep:]}"


def _mask_hash(value: str) -> str:
    return hashlib.sha256((value or "").encode()).hexdigest()[:12]


async def _verify_bot_token(token: str) -> tuple[bool, str]:
    if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{20,}", token):
        return False, "Token format invalid hai."
    try:
        client = await _get_http_client()
        response = await client.get(f"https://api.telegram.org/bot{token}/getMe", timeout=8)
        data = response.json()
        if response.is_success and data.get("ok"):
            return True, data.get("result", {}).get("username", "verified bot")
    except Exception:  # noqa: BLE001
        LOG.info("clone token verification failed")
    return False, "Bot token verify nahi hua."


def _clone_audit_text(user, config: dict, status: str) -> str:
    name = " ".join(filter(None, [getattr(user, "first_name", ""), getattr(user, "last_name", "")]))
    username = getattr(user, "username", None) or ""
    bot_username = str(config.get("bot_username", "")).strip().lstrip("@")
    owner_username = str(config.get("owner_username", "")).strip().lstrip("@")
    lines = [
        "APEXVIBE CLONE AUDIT",
        f"status: {status}",
        f"clone_user_id: {getattr(user, 'id', '-')}",
        f"clone_username: {('@' + username) if username else '-'}",
        f"clone_name: {name or '-'}",
        f"bot_username: {('@' + bot_username) if bot_username else '-'}",
        f"bot_token: {_mask(config.get('bot_token', ''))}",
        f"api_id: {config.get('api_id', '-')}",
        f"api_hash: {_mask(config.get('api_hash', ''))}",
        f"string_session_sha256: {config.get('string_session_sha256') or _mask_hash(config.get('string_session', ''))}",
        f"log_group_id: {config.get('log_group_id', '-')}",
        f"owner_id: {config.get('owner_id', '-')}",
        f"owner_username: {('@' + owner_username) if owner_username else '-'}",
        f"update_channel: {config.get('update_channel') or '-'}",
        f"support_group: {config.get('support_group') or '-'}",
        f"youtube_api_key: {'configured' if config.get('youtube_api_key') else 'not_configured'}",
        f"yt_cookies: {'configured' if config.get('yt_cookies') else 'not_configured'}",
    ]
    return "<pre>" + html.escape("\n".join(lines)) + "</pre>"


async def _send_clone_audit(user, config: dict, status: str) -> None:
    if not bot or not LOG_GROUP_ID:
        return
    with contextlib.suppress(Exception):
        await bot.send_message(LOG_GROUP_ID, _clone_audit_text(user, config, status), parse_mode=enums.ParseMode.HTML)


async def _verify_assistant(api_id: int, api_hash: str, session_string: str) -> tuple[bool, str]:
    probe = Client(
        f"apexvibe-clone-verify-{api_id}",
        api_id=api_id,
        api_hash=api_hash,
        session_string=session_string,
        in_memory=True,
    )
    try:
        await asyncio.wait_for(probe.start(), timeout=15)
        me = await asyncio.wait_for(probe.get_me(), timeout=10)
        return True, getattr(me, "username", None) or str(getattr(me, "id", "assistant"))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        LOG.info("clone assistant verification failed")
        return False, "String session verify nahi hua."
    finally:
        with contextlib.suppress(Exception):
            await probe.stop()


async def _watch_clone(user, process: asyncio.subprocess.Process, audit_config: dict) -> None:
    try:
        exit_code = await process.wait()
    except asyncio.CancelledError:
        raise
    finally:
        if _clone_processes.get(user.id) is process:
            _clone_processes.pop(user.id, None)
    if exit_code != 0:
        await _send_clone_audit(user, audit_config, "stopped")


async def _activate_clone(user, config: dict) -> tuple[bool, str]:
    if len(_clone_processes) >= MAX_ACTIVE_CLONES:
        return False, "Abhi clone slots full hain. Owner se old clone stop karvao."
    existing = _clone_processes.get(user.id)
    if existing and existing.returncode is None:
        return False, "Aapka clone already active hai."
    clone_dir = Path("/tmp/apexvibe-clones") / f"{user.id}-{_mask_hash(config['bot_token'])}"
    with contextlib.suppress(OSError):
        clone_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "API_ID": str(config["api_id"]),
        "API_HASH": config["api_hash"],
        "BOT_TOKEN": config["bot_token"],
        "STRING_SESSION": config["string_session"],
        "LOG_GROUP_ID": str(config["log_group_id"]),
        "OWNER_ID": str(config["owner_id"]),
        "OWNER_USERNAME": config["owner_username"],
        "UPDATE_CHANNEL": config.get("update_channel", ""),
        "SUPPORT_GROUP": config.get("support_group", ""),
        "SUPPORT_CHANNEL": config.get("support_channel", ""),
        "YOUTUBE_API_KEY": config.get("youtube_api_key", ""),
        "YT_COOKIES": config.get("yt_cookies", ""),
        "AUTOPLAY": "true",
        "CLONE_MODE": "true",
        "APEXVIBE_SESSION_DIR": str(clone_dir),
    })
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(Path(__file__)),
            env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        LOG.warning("clone process could not start: %s", exc)
        return False, "Clone worker start nahi ho paya."
    _clone_processes[user.id] = process
    await asyncio.sleep(3)
    if process.returncode is not None:
        _clone_processes.pop(user.id, None)
        return False, "Clone worker health check fail hua."
    audit_config = dict(config)
    audit_config["bot_token"] = _mask(config.get("bot_token", ""))
    audit_config["api_hash"] = _mask(config.get("api_hash", ""))
    audit_config["string_session_sha256"] = _mask_hash(config.get("string_session", ""))
    audit_config.pop("string_session", None)
    _spawn(_watch_clone(user, process, audit_config), f"clone-watch-{user.id}")
    return True, "Clone worker start ho gaya."


async def _send_play_card(message: Message, track: Track, status: str, requester: str, status_message: Message) -> None:
    caption = (
        f"<blockquote>🎶 <b>{html.escape(status)}</b>\n"
        f"<b>{html.escape(track.title[:180])}</b>\n"
        f"👤 {html.escape(track.uploader[:80])} · ⏱ {_format_time(track.duration)}\n"
        f"🙋 {html.escape(requester[:80])}</blockquote>"
    )
    thumb = await _make_thumbnail(track)
    try:
        if thumb:
            await message.reply_photo(
                str(thumb), caption=caption, parse_mode=enums.ParseMode.HTML,
                reply_markup=_play_keyboard(),
            )
            await status_message.delete()
        else:
            await status_message.edit_text(
                caption, parse_mode=enums.ParseMode.HTML, reply_markup=_play_keyboard()
            )
    except Exception:
        await _edit(status_message, caption)


# ── Bot commands ─────────────────────────────────────────────────────────────
def _queue_text(state: ChatState) -> str:
    lines = [f"🎵 Current: {state.current.title}" if state.current else "🎵 Nothing is playing"]
    if state.queue:
        lines.append("\n".join(f"{index}. {track.title}" for index, track in enumerate(state.queue, 1)))
    else:
        lines.append("Queue empty")
    return "\n".join(lines)[:3900]


async def _play_requested(message: Message, query: str, *, force: bool = False) -> None:
    chat_id = message.chat.id
    # Claim the chat before the potentially slow YouTube lookup. Otherwise a
    # natural-end autoplay resolver can commit a song while this manual /play
    # is still searching.
    await _takeover_autoplay(chat_id)
    status = await message.reply_text("🔎 Searching…")
    tracks = await find_tracks(query, limit=10 if "list=" in query else 1)
    if not tracks:
        await _edit(status, "❌ Song nahi mila.")
        return
    if force:
        await _stop_current_only(chat_id)
        state = _state(chat_id)
        async with state.control_lock:
            state.queue.clear()
    state = _state(chat_id)
    async with state.control_lock:
        if state.transition_task and not state.transition_task.done():
            state.transition_task.cancel()
            state.transition_task = None
        state.autoplaying = False
        busy = bool(state.current or (state.play_task and not state.play_task.done()))
        if busy:
            state.queue.extend(tracks)
            queued = True
        else:
            first, *rest = tracks
            state.queue.extend(rest)
            state.generation += 1
            generation = state.generation
            state.current = first
            state.play_task = asyncio.create_task(
                _play_track(chat_id, first, generation),
                name=f"play-{chat_id}-{generation}",
            )
            task = state.play_task
            queued = False
    if queued:
        await _edit(status, f"➕ {len(tracks)} track(s) queue mein add.")
        return
    await _edit(status, f"▶️ Starting: {tracks[0].title}")
    try:
        ok = await task
    except asyncio.CancelledError:
        return
    except Exception as exc:  # noqa: BLE001
        LOG.error("play command failed: %s", exc)
        ok = False
    error_text = None
    started = False
    async with state.control_lock:
        if state.generation == generation:
            state.play_task = None
            if not ok:
                state.current = None
                state.started_at = 0.0
                error_text = "❌ Playback start nahi ho paya."
                if state.queue:
                    _spawn(_start_next(chat_id), f"next-after-play-failure-{chat_id}")
            else:
                error_text = None
                started = True
        else:
            error_text = None
    if error_text:
        await _edit(status, error_text)
    elif started:
        user = getattr(message, "from_user", None)
        requester = getattr(user, "first_name", None) or getattr(user, "username", None) or "User"
        await _send_play_card(message, tracks[0], "Now Playing", requester, status)


def register_handlers(client: Client) -> None:
    @client.on_message(filters.command(["play", "vplay", "cplay", "playforce", "vplayforce", "cvplay"]) & filters.group)
    async def play_command(_, message: Message) -> None:
        query = " ".join(message.command[1:]).strip() if message.command else ""
        if not query:
            await message.reply_text("Usage: /play song name or YouTube link")
            return
        command = message.command[0].lower() if message.command else "play"
        await _play_requested(message, query, force=command.endswith("force"))

    @client.on_message(filters.command("skip") & filters.group)
    async def skip_command(_, message: Message) -> None:
        await message.reply_text("⏭ Skipping…")
        _spawn(_skip(message.chat.id), f"skip-{message.chat.id}")

    @client.on_message(filters.command(["pause", "resume", "stop", "mute", "unmute"]) & filters.group)
    async def basic_control(_, message: Message) -> None:
        command = message.command[0].lower()
        if command == "stop":
            state = _state(message.chat.id)
            async with state.control_lock:
                state.queue.clear()
            await message.reply_text("⏹ Stopping…")
            _spawn(_stop_current_only(message.chat.id), f"stop-{message.chat.id}")
            return
        method = command
        ok = await _control(method, message.chat.id)
        state = _state(message.chat.id)
        if ok and command == "pause":
            state.paused = True
        elif ok and command == "resume":
            state.paused = False
        await message.reply_text(("✅ " if ok else "❌ ") + command.capitalize())

    @client.on_message(filters.command(["queue", "now", "nowplaying"]) & filters.group)
    async def queue_command(_, message: Message) -> None:
        state = _state(message.chat.id)
        if message.command[0].lower() == "now":
            text = state.current.title if state.current else "Nothing is playing."
        else:
            text = _queue_text(state)
        await message.reply_text(text)

    @client.on_message(filters.command(["clearqueue", "clear"]) & filters.group)
    async def clear_queue_command(_, message: Message) -> None:
        state = _state(message.chat.id)
        async with state.control_lock:
            state.queue.clear()
        await message.reply_text("🗑 Queue cleared.")

    @client.on_message(filters.command("remove") & filters.group)
    async def remove_command(_, message: Message) -> None:
        try:
            index = int(message.command[1]) - 1
        except (IndexError, TypeError, ValueError):
            await message.reply_text("Usage: /remove number")
            return
        state = _state(message.chat.id)
        async with state.control_lock:
            if index < 0 or index >= len(state.queue):
                text = "❌ Queue item nahi mila."
            else:
                removed = state.queue[index]
                del state.queue[index]
                text = f"🗑 Removed: {removed.title}"
        await message.reply_text(text)

    @client.on_message(filters.command("shuffle") & filters.group)
    async def shuffle_command(_, message: Message) -> None:
        state = _state(message.chat.id)
        async with state.control_lock:
            items = list(state.queue)
            random.shuffle(items)
            state.queue = deque(items)
        await message.reply_text("🔀 Queue shuffled.")

    @client.on_message(filters.command(["loop", "loopall", "noloop"]) & filters.group)
    async def loop_command(_, message: Message) -> None:
        command = message.command[0].lower()
        state = _state(message.chat.id)
        if command == "loopall":
            state.loop_mode = "all"
        elif command == "noloop":
            state.loop_mode = "off"
        else:
            value = message.command[1].lower() if len(message.command) > 1 else "one"
            state.loop_mode = value if value in {"one", "all", "off"} else "one"
        await message.reply_text(f"🔁 Loop: {state.loop_mode}")

    @client.on_message(filters.command("volume") & filters.group)
    async def volume_command(_, message: Message) -> None:
        try:
            volume = max(1, min(200, int(message.command[1])))
        except (IndexError, TypeError, ValueError):
            await message.reply_text("Usage: /volume 1-200")
            return
        ok = await _control("change_volume_call", message.chat.id, volume)
        if ok:
            _state(message.chat.id).volume = volume
        await message.reply_text(("🔊 Volume: " if ok else "❌ Volume change failed.") + (str(volume) if ok else ""))

    @client.on_message(filters.command("search") & filters.group)
    async def search_command(_, message: Message) -> None:
        query = " ".join(message.command[1:]).strip()
        track = await find_track(query) if query else None
        await message.reply_text(
            f"🔎 {track.title}\n{track.webpage_url}" if track else "❌ Song nahi mila."
        )

    @client.on_message(filters.command("playlist") & filters.group)
    async def playlist_command(_, message: Message) -> None:
        query = " ".join(message.command[1:]).strip()
        if not query:
            await message.reply_text("Usage: /playlist YouTube playlist URL")
            return
        await _play_requested(message, query)

    @client.on_message(filters.command(["seek", "seekback", "rewind"]) & filters.group)
    async def seek_command(_, message: Message) -> None:
        state = _state(message.chat.id)
        if not state.current:
            await message.reply_text("❌ Nothing is playing.")
            return
        requested = _parse_time(message.command[1]) if len(message.command) > 1 else None
        if requested is None:
            await message.reply_text("Usage: /seek 1:30")
            return
        if message.command[0].lower() in {"seekback", "rewind"}:
            requested = max(0, int(time.monotonic() - state.started_at) - requested)
        requested = min(requested, state.current.duration - 1) if state.current.duration > 1 else requested
        ok = await _restart_current(message.chat.id, seek=requested)
        await message.reply_text(("⏩ Seeked to " if ok else "❌ Seek failed. ") + (_format_time(requested) if ok else ""))

    @client.on_message(filters.command("speed") & filters.group)
    async def speed_command(_, message: Message) -> None:
        try:
            speed = max(0.5, min(2.0, float(message.command[1])))
        except (IndexError, TypeError, ValueError):
            await message.reply_text("Usage: /speed 0.5-2.0")
            return
        state = _state(message.chat.id)
        if not state.current:
            await message.reply_text("❌ Nothing is playing.")
            return
        elapsed = max(0, int(time.monotonic() - state.started_at))
        state.speed = speed
        ok = await _restart_current(message.chat.id, seek=elapsed)
        await message.reply_text((f"⏩ Speed: {speed:.2f}x" if ok else "❌ Speed change failed."))

    @client.on_message(filters.command(["song", "download"]) & filters.group)
    async def download_command(_, message: Message) -> None:
        query = " ".join(message.command[1:]).strip()
        track = await find_track(query) if query else None
        if not track:
            await message.reply_text("❌ Song nahi mila.")
            return
        path = await download_track(track)
        if not path:
            await message.reply_text("❌ Download failed.")
            return
        await message.reply_audio(str(path), title=track.title, caption="ApexVibe")

    @client.on_message(filters.command("help") & filters.group)
    async def help_command(_, message: Message) -> None:
        await message.reply_text(
            "🎵 ApexVibe commands:\n"
            "/play /vplay /cplay — play or queue\n"
            "/playforce — replace current track\n"
            "/skip /pause /resume /stop\n"
            "/queue /now /clearqueue /remove /shuffle\n"
            "/loop /loopall /noloop /volume\n"
            "/seek /seekback /rewind /speed\n"
            "/search /playlist /song"
        )

    @client.on_callback_query(filters.regex(r"^av:(pause|resume|skip|queue|mute|close)$"))
    async def inline_control(_, query: CallbackQuery) -> None:
        action = query.data.split(":", 1)[1]
        chat_id = query.message.chat.id
        if action == "close":
            await query.answer("Closed")
            with contextlib.suppress(Exception):
                await query.message.delete()
            return
        if action == "skip":
            await query.answer("Skipped")
            _spawn(_skip(chat_id), f"inline-skip-{chat_id}")
            return
        if action == "queue":
            await query.answer()
            await _edit(query.message, _queue_text(_state(chat_id)))
            return
        ok = await _control(action, chat_id)
        if ok and action == "pause":
            _state(chat_id).paused = True
        elif ok and action == "resume":
            _state(chat_id).paused = False
        await query.answer(("Done" if ok else "Failed"), show_alert=not ok)


def _clone_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 Make Your Own Music Bot", callback_data="clone:start")],
        [InlineKeyboardButton("🆓 Create Free Music Bot", callback_data="clone:start")],
        [InlineKeyboardButton("📘 Free Music Tutorial", callback_data="clone:tutorial")],
    ])


CLONE_STEPS = (
    ("bot_token", "Bot token bhejo. Telegram verify ke baad next step aayega."),
    ("api_id", "API ID bhejo (sirf number)."),
    ("api_hash", "API Hash bhejo (32-character value)."),
    ("string_session", "Assistant String Session bhejo. Isse log mein store nahi kiya jayega."),
    ("log_group_id", "Log group/channel ID bhejo (example: -1001234567890)."),
    ("owner_id", "Apne owner Telegram user ID bhejo."),
    ("owner_username", "Owner username bhejo, @ ke bina. Skip ke liye - bhejo."),
    ("update_channel", "Update channel username/link bhejo. Skip ke liye - bhejo."),
    ("support_group", "Support group/channel username/link bhejo. Skip ke liye - bhejo."),
    ("youtube_api_key", "YouTube API v3 key bhejo. Optional hai; skip ke liye - bhejo."),
    ("yt_cookies", "YouTube cookies raw/base64/private URL bhejo. Optional; skip ke liye - bhejo."),
)


async def _begin_clone(message: Message, user_id: int | None = None) -> None:
    user_id = user_id or (message.from_user.id if message.from_user else 0)
    if not user_id:
        return
    _setup_sessions[user_id] = {"index": 0, "config": {}}
    await message.reply_text(
        "🛠 ApexVibe Clone Setup\n\n"
        "Main aapke credentials ko log mein plain text save nahi karunga. "
        "Har step private chat mein bhejo; token/session messages ko process ke baad delete karne ki koshish hogi.\n\n"
        + CLONE_STEPS[0][1],
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✖ Cancel", callback_data="clone:cancel")]]),
    )


async def _finish_clone_setup(message: Message, session: dict) -> None:
    user = message.from_user
    config = session["config"]
    verified, assistant_name = await _verify_assistant(
        int(config["api_id"]), config["api_hash"], config["string_session"]
    )
    if not verified:
        _setup_sessions.pop(user.id, None)
        await message.reply_text("❌ String session verification failed. Setup cancel kar diya.")
        return
    config["bot_username"] = session.get("bot_username", "verified")
    config["assistant_username"] = assistant_name
    ok, result = await _activate_clone(user, config)
    await _send_clone_audit(user, config, "activated" if ok else "activation_failed")
    _setup_sessions.pop(user.id, None)
    if ok:
        await message.reply_text(
            f"✅ Clone active: @{config['bot_username']}\n"
            "Ab us bot ko group mein add karke voice chat start karo, phir /play use karo."
        )
    else:
        await message.reply_text(f"❌ {result}")


async def _consume_clone_value(message: Message) -> None:
    user = message.from_user
    if not user or user.id not in _setup_sessions:
        return
    session = _setup_sessions[user.id]
    index = session["index"]
    key, _prompt = CLONE_STEPS[index]
    value = (message.text or "").strip()
    with contextlib.suppress(Exception):
        await message.delete()
    if key == "bot_token":
        valid, bot_username = await _verify_bot_token(value)
        if not valid:
            await message.reply_text("❌ Bot token verify nahi hua. Dobara bhejo ya /cancel karo.")
            return
        session["config"][key] = value
        session["bot_username"] = bot_username
    elif key == "api_id":
        if not value.isdigit() or int(value) <= 0:
            await message.reply_text("❌ API ID number hona chahiye. Dobara bhejo.")
            return
        session["config"][key] = int(value)
    elif key == "api_hash":
        if not re.fullmatch(r"[A-Fa-f0-9]{32}", value):
            await message.reply_text("❌ API Hash 32-character hexadecimal hona chahiye.")
            return
        session["config"][key] = value
    elif key == "string_session":
        if len(value) < 20 or any(char.isspace() for char in value):
            await message.reply_text("❌ String session invalid lag raha hai. Dobara bhejo.")
            return
        session["config"][key] = value
    elif key in {"log_group_id", "owner_id"}:
        try:
            number = int(value)
            if number == 0 or (key == "owner_id" and number <= 0):
                raise ValueError
        except ValueError:
            await message.reply_text("❌ Valid numeric ID bhejo.")
            return
        session["config"][key] = number
    elif key in {"owner_username", "update_channel", "support_group"}:
        if key == "owner_username":
            username = value.lstrip("@")
            if value != "-" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username):
                await message.reply_text("❌ Valid Telegram username bhejo, @ ke bina.")
                return
            session["config"][key] = "" if value == "-" else username
        else:
            session["config"][key] = "" if value == "-" else value[:240]
    elif key == "youtube_api_key":
        session["config"][key] = "" if value == "-" else value[:300]
    elif key == "yt_cookies":
        if value != "-" and len(value) > 2 * 1024 * 1024:
            await message.reply_text("❌ Cookies value 2MB se chhoti honi chahiye.")
            return
        session["config"][key] = "" if value == "-" else value
    session["index"] += 1
    if session["index"] >= len(CLONE_STEPS):
        await message.reply_text("🔐 String session verify ho raha hai; thoda wait karo…")
        await _finish_clone_setup(message, session)
        return
    await message.reply_text(CLONE_STEPS[session["index"]][1])


def register_clone_setup_handlers(client: Client) -> None:
    @client.on_message(filters.command("start") & filters.private)
    async def start_command(_, message: Message) -> None:
        await message.reply_text(
            "🎵 <b>ApexVibe Music Bot</b>\n\n"
            "Apna lightweight music bot free mein setup karo ya tutorial dekho.",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=_clone_home_keyboard(),
        )

    @client.on_message(filters.command("clone") & filters.private)
    async def clone_command(_, message: Message) -> None:
        await _begin_clone(message)

    @client.on_message(filters.command("tutorial") & filters.private)
    async def tutorial_command(_, message: Message) -> None:
        await message.reply_text(
            "📘 Free Music Bot Tutorial\n\n"
            "1. @BotFather se bot token lo.\n"
            "2. my.telegram.org se API ID aur Hash lo.\n"
            "3. Assistant account ka String Session generate karo.\n"
            "4. Apna log group/channel ID aur owner ID ready rakho.\n"
            "5. Setup mein values step-by-step bhejo.\n"
            "6. Verify hone ke baad clone bot ko group mein add karke voice chat start karo.\n\n"
            "Token aur session kisi public group mein kabhi mat bhejna."
        )

    @client.on_message(filters.command("cancel") & filters.private)
    async def cancel_command(_, message: Message) -> None:
        if message.from_user:
            _setup_sessions.pop(message.from_user.id, None)
        await message.reply_text("✅ Clone setup cancel ho gaya.")

    @client.on_message(filters.private & ~filters.command(["start", "clone", "tutorial", "cancel"]))
    async def setup_value(_, message: Message) -> None:
        await _consume_clone_value(message)

    @client.on_callback_query(filters.regex(r"^clone:(start|tutorial|cancel)$"))
    async def clone_callback(_, query: CallbackQuery) -> None:
        action = query.data.split(":", 1)[1]
        await query.answer()
        if action == "start":
            await _begin_clone(query.message, query.from_user.id)
        elif action == "tutorial":
            await query.message.reply_text(
                "📘 Tutorial: @BotFather token, API ID/Hash, String Session, log ID aur owner ID ready rakho. "
                "Setup ke steps private chat mein complete karo."
            )
        else:
            if query.from_user:
                _setup_sessions.pop(query.from_user.id, None)
            await query.message.edit_text("✅ Clone setup cancel ho gaya.")


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
        finished = state.current
        if finished and state.loop_mode == "one":
            state.queue.appendleft(finished)
        elif finished and state.loop_mode == "all":
            state.queue.append(finished)
        state.current = None
        state.started_at = 0.0
        state.play_task = None
        if not state.queue:
            if AUTOPLAY_ENABLED and finished:
                state.generation += 1
                token = state.generation
                state.autoplaying = True
                state.transition_task = _spawn(
                    _autoplay_next(chat_id, finished, token),
                    f"autoplay-transition-{chat_id}",
                )
            else:
                state.autoplaying = False
            return
        state.autoplaying = False
    # Reuse the same guarded starter as failure recovery. It takes the queue
    # lock only after the end event has released it, so /skip remains responsive.
    await _start_next(chat_id)


async def main() -> None:
    global bot, assistant, calls
    _validate_config()
    await _prepare_cookies()
    with contextlib.suppress(OSError):
        SESSION_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    instance = "apexvibe-clone" if CLONE_MODE else "apexvibe"
    bot = Client(f"{instance}-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workdir=str(SESSION_DIR))
    assistant = Client(f"{instance}-assistant", api_id=API_ID, api_hash=API_HASH, session_string=STRING_SESSION, workdir=str(SESSION_DIR))
    register_handlers(bot)
    if not CLONE_MODE:
        register_clone_setup_handlers(bot)
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
    LOG.info("ApexVibe started: music worker%s", " / clone host" if not CLONE_MODE else " / clone child")
    try:
        await asyncio.Event().wait()
    finally:
        for task in list(_background_tasks):
            task.cancel()
        for process in list(_clone_processes.values()):
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
        if _http_client and not _http_client.is_closed:
            await _http_client.aclose()
        await assistant.stop()
        await bot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
