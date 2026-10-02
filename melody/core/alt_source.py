"""
Non-YouTube audio fallback (JioSaavn -> SoundCloud).

ROOT-CAUSE FIX for "gana play nahi ho raha" / "Sign in to confirm you're not
a bot" coming back again and again even with YT_COOKIES + YOUTUBE_API_KEY:

  * YOUTUBE_API_KEY (Data API v3) only returns SEARCH + METADATA. It never
    returns a playable audio URL, so it cannot fix playback at all.
  * Heroku/cloud IP ranges are flagged by YouTube. A cookie jar replayed from
    such an IP gets flagged within minutes ("YT_COOKIES rejected by YouTube"
    in the worker log), and then every client answers with the bot wall or a
    storyboard-only format list ("Requested format is not available").

No amount of retry rungs inside yt-dlp can beat an IP-level block. The only
in-code root fix is to stop depending on YouTube for the AUDIO BYTES: keep
using YouTube for search/metadata (that still works through API v3 /
InnerTube), and when YouTube refuses to hand out media, fetch the same song
from a source that does not bot-wall datacenter IPs:

  1. JioSaavn  — huge Bollywood/Punjabi/Indian catalogue, 320 kbps AAC,
                 plain HTTPS, no auth, no cookies.
  2. SoundCloud — via yt-dlp's soundcloud extractor (scsearch), works from
                 cloud IPs without login.

A short circuit breaker remembers that YouTube is blocking this host, so the
next /play goes to the fallback FIRST (instant) instead of burning the whole
yt-dlp ladder again before falling back.

Env knobs:
  ALT_SOURCE=0                    disable this module entirely
  ALT_SOURCE_ORDER=jiosaavn,soundcloud
  ALT_SOURCE_BLOCK_TTL=900        seconds to prefer the fallback after a wall
  ALT_SAAVN_BITRATE=320           320 / 160 / 96
"""
from __future__ import annotations

import asyncio
import html
import os
import difflib
import re
import time
from typing import Optional

import httpx

from melody.logging import LOGGER

_SAAVN_API = "https://www.jiosaavn.com/api.php"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
# The signed web.saavncdn.com URL answers 403 without this Referer.
_SAAVN_HEADERS = {"User-Agent": _UA, "Referer": "https://www.jiosaavn.com/"}

_MIN_BYTES = 64 * 1024

# ── circuit breaker ──────────────────────────────────────────────────────
_yt_blocked_until = 0.0
_ALT_FILES: set = set()


def is_audio_only_file(path) -> bool:
    """True for files with no video track (alt-source or audio-only cache).

    /vplay falls back to these when YouTube is walled; call.py must then
    build an audio-only MediaStream or PyTgCalls dies on NoVideoSourceFound.
    """
    if not path:
        return False
    p = str(path)
    if p in _ALT_FILES:
        return True
    base = os.path.basename(p)
    return bool(re.search(r"_a\.[A-Za-z0-9]+$", base))


def enabled() -> bool:
    return os.getenv("ALT_SOURCE", "1").strip().lower() not in {"0", "false", "no", "off"}


def _block_ttl() -> float:
    try:
        return max(60.0, float(os.getenv("ALT_SOURCE_BLOCK_TTL", "900")))
    except ValueError:
        return 900.0


def mark_youtube_blocked(reason: str = "") -> None:
    """Remember that YouTube walled this host; prefer the fallback for a while."""
    global _yt_blocked_until
    first = time.monotonic() >= _yt_blocked_until
    _yt_blocked_until = time.monotonic() + _block_ttl()
    if first:
        LOGGER.warning(
            "🧱 YouTube is blocking media on this host (%s) — next %ds every /play "
            "uses JioSaavn/SoundCloud audio first.",
            (reason or "bot wall")[:80], int(_block_ttl()),
        )


def youtube_blocked() -> bool:
    return enabled() and time.monotonic() < _yt_blocked_until


def clear_youtube_block() -> None:
    global _yt_blocked_until
    _yt_blocked_until = 0.0


# ── title cleaning / matching ────────────────────────────────────────────
_NOISE_RE = re.compile(
    r"\b(official|video|music|audio|lyrics?|lyrical|full|song|songs|hd|4k|hq|"
    r"new|latest|punjabi|hindi|bollywood|remix(?:ed)?|visualizer|teaser|"
    r"trailer|status|slowed|reverb|lofi|lo-fi|version|feat|ft|prod|by|from|"
    r"movie|film|the|t-series|tseries|vevo|records|official\s*music)\b",
    re.I,
)
_BRACKET_RE = re.compile(r"[\(\[\{【].*?[\)\]\}】]")
_SPLIT_RE = re.compile(r"\s[|•·]\s|\s-{1,2}\s|//")


def clean_title(title: str) -> str:
    """'Gal Karke (Official Video) | Asees Kaur | T-Series' -> 'Gal Karke Asees Kaur'."""
    t = (title or "").replace("&amp;", "&")
    t = _BRACKET_RE.sub(" ", t)
    parts = [p.strip() for p in _SPLIT_RE.split(t) if p.strip()]
    # Song name + first credited artist is the best search key.
    t = " ".join(parts[:2]) if parts else t
    t = re.sub(r"[#@\"'“”‘’:;,!?/\\]+", " ", t)
    t = _NOISE_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9\u0900-\u097f]+", (text or "").lower()) if len(w) > 1}


def _norm_tok(w: str) -> str:
    # Collapse common Hinglish/Marathi transliteration variants so
    # 'laal'=='lal', 'sangaychay'=='sangaichay', 'bangadi'=='bangdi'.
    w = re.sub(r"(.)\1+", r"\1", w)          # laal -> lal, mulkk -> mulk
    w = w.replace("y", "i").replace("w", "v").replace("ee", "i").replace("oo", "u")
    w = re.sub(r"(?<=.)[aeiou]", "", w)        # drop inner vowels (keep first char)
    return w


def _tok_match(a: str, b: str) -> bool:
    if a == b:
        return True
    na, nb = _norm_tok(a), _norm_tok(b)
    if na == nb and len(na) >= 2:
        return True
    if min(len(a), len(b)) >= 4 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8:
        return True
    return False


def _overlap(q: set, c: set, c_text: str) -> float:
    """Fuzzy share of query words found in the candidate (spelling-tolerant)."""
    if not q or not c:
        return 0.0
    joined = re.sub(r"[^a-z0-9\u0900-\u097f]", "", (c_text or "").lower())
    hits = 0
    for w in q:
        if any(_tok_match(w, x) for x in c) or (len(w) >= 6 and w in joined):
            hits += 1
    q_score = hits / len(q)
    c_score = hits / max(1, len(c))
    # Query words mostly present -> match; also reward short exact candidates.
    return max(q_score, min(1.0, c_score * 1.2)) if hits else 0.0


def _score(query: str, cand_title: str, want_dur: int, cand_dur: int, relaxed: bool = False) -> float:
    q, c = _tokens(query), _tokens(cand_title)
    if not q or not c:
        return 0.0
    overlap = _overlap(q, c, cand_title)
    # Song name must match, not just the artist: the first word of the query
    # (always the song title after clean_title) has to be in the candidate.
    first = next((w for w in re.findall(r"[a-z0-9\u0900-\u097f]+", query.lower()) if len(w) > 1), "")
    if first and not any(_tok_match(first, x) for x in c):
        return 0.0
    min_overlap = 0.34 if relaxed else 0.5
    if overlap < min_overlap:
        return 0.0  # title does not match — never play a random song
    score = overlap
    if want_dur and cand_dur:
        diff = abs(int(want_dur) - int(cand_dur))
        tol = max(20, int(want_dur * 0.15))
        max_diff = tol * 6 if relaxed else tol * 3
        if diff > max_diff:
            # Official videos often carry long intros/outros. A very strong
            # title match is still the same song; anything weaker is not.
            if overlap < 0.8 or diff > max(240, want_dur * 0.6):
                return 0.0
            score -= 0.2
        elif diff <= tol:
            score += 0.5
    return score


def song_core(title: str) -> str:
    """Just the song name: 'Tu Rehnuma. Le Gaana Baaja | Love...' -> 'Tu Rehnuma'."""
    t = (title or "").replace("&amp;", "&")
    t = _BRACKET_RE.sub(" ", t)
    parts = [p.strip() for p in _SPLIT_RE.split(t) if p.strip()]
    t = parts[0] if parts else t
    t = re.split(r"[.|]", t)[0]
    t = re.sub(r"[#@\"'“”‘’:;,!?/\\]+", " ", t)
    t = re.sub(r"\b(official|video|song|full|audio|lyrical|lyrics|hd|4k)\b", " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def _good_enough(score: float, want_dur: int) -> bool:
    return score >= 0.5


_RELAXED_THRESHOLD = 0.3


def _good_enough_relaxed(score: float, want_dur: int) -> bool:
    return score >= _RELAXED_THRESHOLD


# ── JioSaavn ─────────────────────────────────────────────────────────────
async def _saavn_candidates(client: httpx.AsyncClient, query: str) -> list:
    params = {
        "__call": "search.getResults", "p": 1, "n": 6, "q": query,
        "_format": "json", "_marker": 0, "api_version": 4, "ctx": "web6dot0",
    }
    r = await client.get(_SAAVN_API, params=params, headers=_SAAVN_HEADERS)
    r.raise_for_status()
    out = []
    for s in (r.json() or {}).get("results") or []:
        mi = s.get("more_info") or {}
        enc = mi.get("encrypted_media_url")
        if not enc:
            continue
        try:
            dur = int(mi.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0
        artists = ""
        try:
            artists = " ".join(a.get("name", "") for a in (mi.get("artistMap") or {}).get("primary_artists") or [])
        except Exception:  # noqa: BLE001
            pass
        out.append({
            "title": html.unescape(f"{s.get('title', '')} {artists or s.get('subtitle', '')}"),
            "duration": dur, "enc": enc,
        })
    return out


async def _saavn_auth_url(client: httpx.AsyncClient, enc: str, bitrate: str) -> Optional[str]:
    params = {
        "__call": "song.generateAuthToken", "url": enc, "bitrate": bitrate,
        "api_version": 4, "_format": "json", "ctx": "web6dot0", "_marker": 0,
    }
    r = await client.get(_SAAVN_API, params=params, headers=_SAAVN_HEADERS)
    if r.status_code != 200:
        return None
    url = (r.json() or {}).get("auth_url")
    return url if url and url.startswith("https://") else None


async def _stream_to_file(client, url, headers, dest_tmp, cancel_event, budget) -> int:
    started = time.monotonic()
    written = 0
    async with client.stream("GET", url, headers=headers) as resp:
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}")
        ctype = resp.headers.get("content-type", "")
        if "text/html" in ctype:
            raise RuntimeError(f"unexpected content-type {ctype}")
        with open(dest_tmp, "wb") as fh:
            async for chunk in resp.aiter_bytes(64 * 1024):
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError("superseded")
                if time.monotonic() - started > budget:
                    raise asyncio.TimeoutError("alt download budget exceeded")
                fh.write(chunk)
                written += len(chunk)
    return written


async def _try_jiosaavn(query, want_dur, final_base, cancel_event, relaxed=False) -> Optional[str]:
    timeout = httpx.Timeout(30.0, connect=6.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        cands = await _saavn_candidates(client, query)
        if not cands:
            return None
        ranked = sorted(
            ((_score(query, c["title"], want_dur, c["duration"], relaxed=relaxed), c) for c in cands),
            key=lambda x: x[0], reverse=True,
        )
        best_score, best = ranked[0]
        threshold_fn = _good_enough_relaxed if relaxed else _good_enough
        if not threshold_fn(best_score, want_dur):
            LOGGER.info("alt/jiosaavn: no confident match for %r (best=%.2f %r) relaxed=%s",
                        query, best_score, best["title"][:60], relaxed)
            return None
        pref = os.getenv("ALT_SAAVN_BITRATE", "320").strip() or "320"
        tmp = f"{final_base}.alt.part"
        for bitrate in dict.fromkeys([pref, "160", "96"]):
            url = await _saavn_auth_url(client, best["enc"], bitrate)
            if not url:
                continue
            try:
                n = await _stream_to_file(client, url, _SAAVN_HEADERS, tmp, cancel_event, 45.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                _rm(tmp)
                raise
            except Exception as exc:  # noqa: BLE001
                LOGGER.info("alt/jiosaavn %skbps fetch failed: %s", bitrate, exc)
                _rm(tmp)
                continue
            if n < _MIN_BYTES:
                _rm(tmp)
                continue
            final = f"{final_base}.m4a"
            os.replace(tmp, final)
            LOGGER.info("🎧 alt/jiosaavn matched %r -> %r (%skbps, %.1f MB)",
                        query, best["title"][:60], bitrate, n / 1048576)
            return final
    return None


# ── SoundCloud (yt-dlp) ──────────────────────────────────────────────────
def _soundcloud_sync(query, want_dur, final_base, relaxed=False) -> Optional[str]:
    from yt_dlp import YoutubeDL

    base = {"quiet": True, "no_warnings": True, "noprogress": True,
            "noplaylist": True, "socket_timeout": 10, "retries": 2}
    with YoutubeDL({**base, "extract_flat": "in_playlist"}) as ydl:
        res = ydl.extract_info(f"scsearch5:{query}", download=False) or {}
    entries = [e for e in (res.get("entries") or []) if e and e.get("url")]
    if not entries:
        return None
    ranked = sorted(
        ((_score(query, e.get("title", "") + " " + str(e.get("uploader") or ""),
                 want_dur, int(e.get("duration") or 0), relaxed=relaxed), e) for e in entries),
        key=lambda x: x[0], reverse=True,
    )
    outtmpl = f"{final_base}.altsc.%(ext)s"
    opts = {
        **base, "outtmpl": outtmpl, "fixup": "never", "postprocessors": [],
        # Progressive first (single GET), HLS as backup.
        "format": "bestaudio[protocol=http]/bestaudio[protocol^=http]/bestaudio/best",
    }
    threshold_fn = _good_enough_relaxed if relaxed else _good_enough
    tried = 0
    for score, cand in ranked:
        if not threshold_fn(score, want_dur) or tried >= 3:
            break
        tried += 1
        path = None
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(cand["url"], download=True) or {}
                path = ydl.prepare_filename(info)
        except Exception as exc:  # noqa: BLE001 — DRM / Go+ preview / geo: try next
            LOGGER.info("alt/soundcloud candidate skipped (%s): %s",
                        str(cand.get("title"))[:40], str(exc)[-80:])
            _rm(path)
            continue
        if not path or not os.path.exists(path) or os.path.getsize(path) < _MIN_BYTES:
            _rm(path)
            continue
        ext = os.path.splitext(path)[1] or ".m4a"
        final = f"{final_base}{ext}"
        os.replace(path, final)
        LOGGER.info("🎧 alt/soundcloud matched %r -> %r", query, str(cand.get("title"))[:60])
        return final
    if not tried:
        LOGGER.info("alt/soundcloud: no confident match for %r (relaxed=%s)", query, relaxed)
    return None


async def _try_soundcloud(query, want_dur, final_base, cancel_event, relaxed=False) -> Optional[str]:
    loop = asyncio.get_running_loop()
    try:
        from melody.core.pools import YTDL_POOL as pool
    except Exception:  # noqa: BLE001
        pool = None
    return await asyncio.wait_for(
        loop.run_in_executor(pool, _soundcloud_sync, query, want_dur, final_base, relaxed), 60.0,
    )


def _rm(path) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


_PROVIDERS = {"jiosaavn": _try_jiosaavn, "soundcloud": _try_soundcloud}


async def fetch_alternative_audio(
    video_id: str, tag: str, title: str, uploader: str = "", duration: int = 0,
    cancel_event=None, _relaxed: bool = False,
) -> Optional[str]:
    """Download the same song from a non-YouTube source.

    Writes ``/tmp/melody_<video_id>_<tag>.<ext>`` (the normal cache path, so
    replays hit the cache) and returns it, or None when no confident match.
    """
    if not enabled() or not title or title == "Unknown":
        return None
    query = clean_title(title)
    if len(_tokens(query)) < 1:
        return None
    want_dur = int(duration or 0)
    if want_dur and want_dur > 20 * 60:
        return None  # mixes / jukeboxes / podcasts — no sane single-track match
    final_base = f"/tmp/melody_{video_id}_{tag}"
    order = [p.strip() for p in os.getenv("ALT_SOURCE_ORDER", "jiosaavn,soundcloud").split(",")]
    providers = [(n, _PROVIDERS[n]) for n in order if n in _PROVIDERS]
    if not providers:
        return None
    # SPEED: providers run in PARALLEL. The first one starts immediately,
    # every next one gets a short head-start delay (ALT_STAGGER, default
    # 1.5s) so a fast JioSaavn hit does not waste a SoundCloud search, but a
    # slow/no-match JioSaavn never costs more than ~1.5s.
    stagger = max(0.0, float(os.getenv("ALT_STAGGER", "1.5") or 1.5))
    started = time.monotonic()

    async def _run(idx, name, fn):
        if idx:
            await asyncio.sleep(stagger * idx)
        if cancel_event is not None and cancel_event.is_set():
            return None
        try:
            path = await fn(query, want_dur, final_base, cancel_event, relaxed=_relaxed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.info("alt/%s failed for %s: %s", name, video_id, str(exc)[:200])
            return None
        if path:
            LOGGER.info("✅ alt source %s supplied %s in %.2fs", name, video_id,
                        time.monotonic() - started)
        return path

    tasks = [asyncio.ensure_future(_run(i, n, f)) for i, (n, f) in enumerate(providers)]
    try:
        for fut in asyncio.as_completed(tasks):
            path = await fut
            if path:
                _ALT_FILES.add(path)
                return path
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()

    # RELAXED RETRY: when both providers failed with the strict threshold,
    # retry with a lower threshold and a broader query (raw title without
    # noise-word stripping). This catches songs where clean_title() removed
    # a key word or the alt-source catalogue uses a slightly different name.
    if not _relaxed and os.getenv("ALT_RELAXED_RETRY", "1").strip().lower() not in {"0", "false", "no", "off"}:
        core = song_core(title)
        if core and len(_tokens(core)) >= 1 and clean_title(core) != query:
            LOGGER.info("🔄 alt source relaxed retry for %s with song-name query %r", video_id, core[:60])
            return await fetch_alternative_audio(
                video_id, tag, core, uploader, duration, cancel_event,
                _relaxed=True,
            )
    return None
