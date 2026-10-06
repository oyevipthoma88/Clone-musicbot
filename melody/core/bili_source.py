"""
Bilibili fallback source (audio for /play, muxed MP4 for /vplay).

Why Bilibili: it does NOT bot-wall datacenter IPs, needs no login/cookies for
360p/480p, and serves media from an Akamai mirror (upos-hz-mirrorakam) that is
fast from Heroku/EU/US hosts. Measured from a cloud box: search ~0.7s,
playurl ~0.5s, full 4-min audio (~3.5 MB) downloaded in ~1s.

Flow (no yt-dlp, plain HTTPS):
  1. /x/frontend/finger/spi           -> buvid3 cookie (search answers 412 without it)
  2. /x/web-interface/search/type     -> candidates (title, duration, bvid)
  3. /x/web-interface/view            -> cid
  4. /x/player/playurl  fnval=16      -> DASH audio (for /play)
     /x/player/playurl  platform=html5 -> single MP4 with audio+video (for /vplay)

Matching re-uses alt_source._score (title + duration check) and additionally
rejects dance covers / reactions / tutorials / compilations, which are very
common on Bilibili and are exactly the "wrong song plays" problem.

Env:
  BILI_ENABLE=1            set 0 to disable
  BILI_VIDEO_QN=32         32=480p, 16=360p (higher needs login)
"""
from __future__ import annotations

import asyncio
import html
import math
import os
import re
import time
from typing import Optional

import httpx

from melody.logging import LOGGER

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA, "Referer": "https://www.bilibili.com/"}
_API = "https://api.bilibili.com"
_MIN_BYTES = 64 * 1024

# Bilibili is full of re-uploads that are NOT the song: dance covers, reaction
# videos, tutorials, "top 10" compilations, live shows. Reject them unless the
# user's own title asks for it.
_BAD_RE = re.compile(
    r"(舞室|翻跳|舞蹈|跳舞|教学|教程|反应|翻唱|伴奏|合集|串烧|现场|混音|改编|鬼畜|速度|倍速)|"
    r"\b(reaction|dance|cover|tutorial|karaoke|instrumental|compilation|"
    r"mashup|live|slowed|reverb|8d|nightcore|remix|jukebox|dj|lofi|sped)\b",
    re.I,
)
_TAG_RE = re.compile(r"<[^>]+>")
_GOOD_RE = re.compile(r"(官方|official|vevo|t-series|tseries|zee music|sony music|"
                      r"saregama|tips|speed records|desi music|lyric|audio|mv|"
                      r"\u97f3\u4e50|原版|无损)", re.I)
_MUSIC_TYPES = {"音乐", "原创音乐", "翻唱", "MV", "音乐现场", "音乐综合", "演奏", "VOCALOID·UTAU"}
_PICK_CACHE: dict = {}
_PICK_TTL = 3600.0

_buvid: dict = {"b3": "", "b4": "", "at": 0.0}
_buvid_lock = asyncio.Lock()


def enabled() -> bool:
    return os.getenv("BILI_ENABLE", "1").strip().lower() not in {"0", "false", "no", "off"}


def _clean(t: str) -> str:
    return html.unescape(_TAG_RE.sub("", t or "")).strip()


def _parse_dur(d) -> int:
    if isinstance(d, (int, float)):
        return int(d)
    try:
        parts = [int(x) for x in str(d).split(":")]
    except ValueError:
        return 0
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


class _SharedClient:
    """Thin proxy so existing `await c.aclose()` calls keep the pooled
    connection alive instead of closing it (TLS reuse = ~300-600ms per /play)."""

    def __init__(self, inner: httpx.AsyncClient):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def aclose(self):  # noqa: D401 - intentionally a no-op
        return None


_shared: dict = {"c": None}


async def _client() -> "httpx.AsyncClient":
    c = _shared["c"]
    if c is None or c.is_closed:
        c = httpx.AsyncClient(
            headers=_HEADERS, follow_redirects=True,
            timeout=httpx.Timeout(12.0, connect=4.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10,
                                keepalive_expiry=120.0),
        )
        _shared["c"] = c
    async with _buvid_lock:
        if not _buvid["b3"] or time.monotonic() - _buvid["at"] > 6 * 3600:
            try:
                r = await c.get(f"{_API}/x/frontend/finger/spi")
                d = (r.json() or {}).get("data") or {}
                _buvid.update(b3=d.get("b_3", ""), b4=d.get("b_4", ""), at=time.monotonic())
            except Exception as exc:  # noqa: BLE001
                LOGGER.info("bili: buvid fetch failed: %s", exc)
    if _buvid["b3"]:
        c.cookies.set("buvid3", _buvid["b3"], domain=".bilibili.com")
    if _buvid["b4"]:
        c.cookies.set("buvid4", _buvid["b4"], domain=".bilibili.com")
    return _SharedClient(c)


async def prewarm() -> None:
    """Open the pooled connection + buvid cookie at startup (first /play fast)."""
    if not enabled():
        return
    try:
        c = await _client()
        await c.get(f"{_API}/x/web-interface/nav")
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("bili prewarm skipped: %s", exc)


async def _search(c: httpx.AsyncClient, query: str) -> list:
    r = await c.get(
        f"{_API}/x/web-interface/search/type",
        params={"search_type": "video", "keyword": query, "page": 1},
    )
    try:
        j = r.json() or {}
    except ValueError:
        j = {}
    if j.get("code") != 0:
        # Stale buvid -> force refresh next time.
        _buvid["b3"] = ""
        return []
    out = []
    for it in ((j.get("data") or {}).get("result") or [])[:12]:
        if not it.get("bvid"):
            continue
        out.append({
            "title": _clean(it.get("title", "")),
            "author": _clean(it.get("author", "")),
            "duration": _parse_dur(it.get("duration")),
            "bvid": it["bvid"],
            "play": int(it.get("play") or 0) if str(it.get("play") or "0").isdigit() else 0,
            "typename": str(it.get("typename") or ""),
        })
    return out


def _rank(query: str, cands: list, want_dur: int, relaxed: bool) -> list:
    from melody.core import alt_source as _alt

    q_bad = bool(_BAD_RE.search(query))
    ranked = []
    for c in cands:
        if not q_bad and _BAD_RE.search(c["title"]):
            continue
        d = c["duration"]
        # Shorts / ringtone clips / hour-long loops are never "the song".
        if d and (d < 45 or (want_dur and d > want_dur * 1.6 + 30)):
            continue
        s = _alt._score(query, c["title"], want_dur, d, relaxed=relaxed)
        if s <= 0:
            continue
        # Bilibili re-uploads: insist on a duration match when we know it.
        if want_dur and d:
            diff = abs(want_dur - d)
            if diff > max(20, int(want_dur * 0.12)):
                s -= 0.4
            elif diff <= 5:
                s += 0.1
        blob = f"{c['title']} {c.get('author', '')}"
        if _GOOD_RE.search(blob):
            s += 0.08
        if c.get("typename") in _MUSIC_TYPES:
            s += 0.05
        # Popularity tiebreak: the real upload beats a 40-view re-upload.
        s += min(0.1, math.log10(max(1, c.get("play", 0))) / 70.0)
        ranked.append((s, c))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked


async def _cid(c: httpx.AsyncClient, bvid: str) -> Optional[int]:
    r = await c.get(f"{_API}/x/web-interface/view", params={"bvid": bvid})
    d = (r.json() or {}).get("data") or {}
    return d.get("cid")


async def _stream(c, url, dest_tmp, cancel_event, budget) -> int:
    started = time.monotonic()
    n = 0
    async with c.stream("GET", url) as resp:
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}")
        with open(dest_tmp, "wb") as fh:
            async for chunk in resp.aiter_bytes(128 * 1024):
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError("superseded")
                if time.monotonic() - started > budget:
                    raise asyncio.TimeoutError("bili download budget exceeded")
                fh.write(chunk)
                n += len(chunk)
    return n


def _rm(p):
    try:
        if p and os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


async def _pick(c, query, want_dur, relaxed):
    from melody.core import alt_source as _alt

    key = (query.lower().strip(), int(want_dur or 0), bool(relaxed))
    hit = _PICK_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _PICK_TTL:
        return hit[1]
    cands = await _search(c, query)
    if not cands:
        return None
    ranked = _rank(query, cands, want_dur, relaxed)
    if not ranked:
        return None
    best_score, best = ranked[0]
    ok = _alt._good_enough_relaxed if relaxed else _alt._good_enough
    if not ok(best_score, want_dur):
        LOGGER.info("alt/bilibili: no confident match for %r (best=%.2f %r)",
                    query, best_score, best["title"][:60])
        return None
    _PICK_CACHE[key] = (time.monotonic(), best)
    if len(_PICK_CACHE) > 500:
        _PICK_CACHE.pop(next(iter(_PICK_CACHE)))
    return best


async def try_audio(query, want_dur, final_base, cancel_event, relaxed=False) -> Optional[str]:
    """alt_source provider signature. Writes <final_base>.m4a."""
    if not enabled():
        return None
    c = await _client()
    try:
        best = await _pick(c, query, want_dur, relaxed)
        if not best:
            return None
        cid = await _cid(c, best["bvid"])
        if not cid:
            return None
        r = await c.get(f"{_API}/x/player/playurl", params={
            "bvid": best["bvid"], "cid": cid, "fnval": 16, "fourk": 0,
        })
        dash = ((r.json() or {}).get("data") or {}).get("dash") or {}
        audios = list(dash.get("audio") or [])
        audios.sort(key=lambda a: abs(int(a.get("bandwidth", 0)) - 132_000))
        tmp = f"{final_base}.bili.part"
        for a in audios[:2]:
            for url in [a.get("baseUrl") or a.get("base_url")] + list(a.get("backupUrl") or a.get("backup_url") or []):
                if not url:
                    continue
                try:
                    n = await _stream(c, url, tmp, cancel_event,
                                      float(os.getenv("BILI_URL_BUDGET", "20")))
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    _rm(tmp)
                    raise
                except Exception as exc:  # noqa: BLE001
                    LOGGER.info("alt/bilibili audio fetch failed: %s", exc)
                    _rm(tmp)
                    continue
                if n < _MIN_BYTES:
                    _rm(tmp)
                    continue
                final = f"{final_base}.m4a"
                os.replace(tmp, final)
                LOGGER.info("🎧 alt/bilibili matched %r -> %r (%.1f MB)",
                            query, best["title"][:60], n / 1048576)
                return final
    finally:
        await c.aclose()
    return None


async def _dash(c, bvid, cid, qn):
    r = await c.get(f"{_API}/x/player/playurl", params={
        "bvid": bvid, "cid": cid, "fnval": 16, "fourk": 0, "qn": qn,
    })
    return ((r.json() or {}).get("data") or {}).get("dash") or {}


def _best_audio(dash):
    a = sorted(dash.get("audio") or [], key=lambda x: abs(int(x.get("bandwidth", 0)) - 132_000))
    return (a[0].get("baseUrl") or a[0].get("base_url")) if a else None


def _best_video(dash, qn):
    # H.264 only: PyTgCalls/ffmpeg re-encode it cheaply; HEVC/AV1 cost CPU.
    vs = [v for v in dash.get("video") or [] if str(v.get("codecs", "")).startswith("avc")]
    if not vs:
        vs = list(dash.get("video") or [])
    if not vs:
        return None
    vs.sort(key=lambda v: (v.get("id", 0) <= int(qn), v.get("id", 0)), reverse=True)
    v = vs[0]
    return v.get("baseUrl") or v.get("base_url")


async def _match(c, title, duration):
    from melody.core import alt_source as _alt

    # All query variants searched in PARALLEL (was sequential: up to 3x ~0.7s).
    # The earliest variant (most specific) wins when several match.
    qs = _alt.query_variants(title)[:3]
    if not qs:
        return None
    res = await asyncio.gather(
        *(_pick(c, q, int(duration or 0), False) for q in qs), return_exceptions=True,
    )
    for r in res:
        if r and not isinstance(r, BaseException):
            return r
    return None


async def resolve_urls(title: str, duration: int = 0, want_video: bool = False) -> Optional[dict]:
    """Direct-stream URLs (no download) in the same shape as
    ytdl.resolve_stream_urls(): {"audio","video","headers","is_live","expires_at"}.
    ffmpeg streams them straight from Akamai -> playback starts in ~2-3s."""
    if not enabled() or not title:
        return None
    qn = os.getenv("BILI_VIDEO_QN", "32").strip() or "32"
    c = await _client()
    try:
        best = await _match(c, title, duration)
        if not best:
            return None
        cid = await _cid(c, best["bvid"])
        if not cid:
            return None
        dash = await _dash(c, best["bvid"], cid, qn)
        audio = _best_audio(dash)
        video = _best_video(dash, qn) if want_video else None
        if not audio or (want_video and not video):
            return None
        LOGGER.info("🔗 alt/bilibili direct %s -> %r",
                    "video+audio" if want_video else "audio", best["title"][:60])
        return {
            "audio": audio, "video": video, "is_live": False,
            "headers": dict(_HEADERS), "source": "bilibili",
            "expires_at": time.time() + 3600,
        }
    finally:
        await c.aclose()


async def fetch_video(video_id: str, title: str, duration: int = 0,
                      cancel_event=None) -> Optional[str]:
    """/vplay download fallback: DASH video (H.264) + audio, muxed by ffmpeg
    (stream copy, no re-encode) into /tmp/melody_<video_id>_v.mp4.

    Note: the html5 single-MP4 URL is cut off after ~300 KB for non-browser
    clients, so DASH is the only reliable path.
    """
    if not enabled() or not title:
        return None
    final_base = f"/tmp/melody_{video_id}_v"
    qn = os.getenv("BILI_VIDEO_DL_QN", "16").strip() or "16"
    c = await _client()
    vtmp, atmp = f"{final_base}.bv.part", f"{final_base}.ba.part"
    try:
        best = await _match(c, title, duration)
        if not best:
            return None
        cid = await _cid(c, best["bvid"])
        if not cid:
            return None
        dash = await _dash(c, best["bvid"], cid, qn)
        vurl, aurl = _best_video(dash, qn), _best_audio(dash)
        if not vurl or not aurl:
            return None
        nv, na = await asyncio.gather(
            _stream(c, vurl, vtmp, cancel_event, 90.0),
            _stream(c, aurl, atmp, cancel_event, 90.0),
        )
        if nv < _MIN_BYTES or na < _MIN_BYTES:
            return None
        final = f"{final_base}.mp4"
        tmp_out = f"{final_base}.mux.tmp.mp4"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-loglevel", "error", "-i", vtmp, "-i", atmp,
            "-c", "copy", "-movflags", "+faststart", tmp_out,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), 60.0)
        if proc.returncode != 0 or not os.path.exists(tmp_out):
            LOGGER.info("alt/bilibili mux failed: %s", (err or b"")[:200])
            _rm(tmp_out)
            return None
        os.replace(tmp_out, final)
        LOGGER.info("🎬 alt/bilibili video %s -> %r (%.1f MB)",
                    video_id, best["title"][:60], os.path.getsize(final) / 1048576)
        return final
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, asyncio.CancelledError):
            raise
        LOGGER.info("alt/bilibili video download failed: %s", str(exc)[:160])
        return None
    finally:
        _rm(vtmp)
        _rm(atmp)
        await c.aclose()
