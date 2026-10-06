"""
YouTube Fast Bypass — Top 5 bots ka combined logic.
Layers: Multi-client (tv/mweb) + JS runtime + cookies + parallel race.
"""
import os, shutil, logging, asyncio
from difflib import SequenceMatcher

LOGGER = logging.getLogger(__name__)

# ============================================================
# PIPED / INVIDIOUS RESOLVER (YouTube video ID se direct audio)
# ============================================================
PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.ducks.party",
    "https://api.piped.private.coffee",
]

INVIDIOUS_INSTANCES = [
    "https://yewtu.be",
    "https://invidious.f5.si",
    "https://inv.vern.cc",
    "https://invidious.nerdvpn.de",
]

async def piped_stream(video_id: str) -> str | None:
    """Piped API se direct audio stream URL nikalo (video ID se exact match)."""
    import aiohttp, asyncio
    async def try_one(inst):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{inst}/streams/{video_id}",
                                 timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200: return None
                    d = await r.json()
                    for f in d.get("audioStreams", []):
                        u = f.get("url")
                        if u and isinstance(u, str) and u.startswith("http"):
                            return u
        except Exception:
            return None
    results = await asyncio.gather(*[try_one(i) for i in PIPED_INSTANCES],
                                   return_exceptions=True)
    for r in results:
        if isinstance(r, str) and r.startswith("http"):
            return r
    return None


async def invidious_stream(video_id: str) -> str | None:
    """Invidious API se direct audio stream URL (video ID se exact match)."""
    import aiohttp, asyncio
    async def try_one(inst):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{inst}/api/v1/videos/{video_id}",
                                 timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200: return None
                    d = await r.json()
                    for f in d.get("adaptiveFormats", []):
                        if str(f.get("type","")).startswith("audio"):
                            u = f.get("url")
                            if u and isinstance(u, str) and u.startswith("http"):
                                return u
        except Exception:
            return None
    results = await asyncio.gather(*[try_one(i) for i in INVIDIOUS_INSTANCES],
                                   return_exceptions=True)
    for r in results:
        if isinstance(r, str) and r.startswith("http"):
            return r
    return None


async def youtube_via_api(video_id: str) -> str | None:
    """YouTube audio stream — Piped + Invidious race (video ID se exact match)."""
    import asyncio
    tasks = [
        asyncio.create_task(piped_stream(video_id)),
        asyncio.create_task(invidious_stream(video_id)),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED, timeout=6)
        for t in done:
            try:
                r = t.result()
                if r and isinstance(r, str) and r.startswith("http"):
                    for p in pending: p.cancel()
                    return r
            except Exception:
                pass
    finally:
        for t in tasks:
            if not t.done(): t.cancel()
    return None



def _clean_title(title: str) -> str:
    """YouTube title se noise hatakar clean query banao."""
    import re
    if not title:
        return ""
    t = title
    # Brackets/parenthesis content hatao
    t = re.sub(r'[\(\[][^\)\]]*[\)\]]', ' ', t)
    # Common noise keywords
    noise = (r'\b(HD|4K|8K|1080p|720p|480p|360p|BluRay|Blu-Ray|BRRip|'
             r'WEB-DL|WEBRip|DVDRip|HDRip|PreDVD|Music Video|Official Video|'
             r'Official Music Video|Lyrical Video|Lyrics Video|Lyrical|Lyrics|'
             r'Full Video|Full Song|Video Song|Audio Song|Audio|'
             r'YouTube|Remastered|Remaster|Mp3|MP3|HQ|Full HD|Ultra HD|'
             r'Theater|Theatre|Print|Version|Edit|Remix|Cover|Live|'
             r'Video|Song|Music|Ft|Feat|Featuring)\b')
    t = re.sub(noise, ' ', t, flags=re.IGNORECASE)
    # Year hatado (2001, 1995, 2024...)
    t = re.sub(r'\b(19|20)\d{2}\b', ' ', t)
    # Extra whitespace collapse
    t = re.sub(r'\s+', ' ', t).strip()
    # Pehle 5 words hi rakho
    words = t.split()[:5]
    return ' '.join(words)



def build_yt_opts(cookiefile: str = None) -> dict:
    """yt-dlp options — SABR bypass + JS runtime + cookies."""
    opts = {
        "format": "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "geo_bypass": True,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 15,
        
        # 🎯 LAYER 1: Multi-client — SABR bypass ke liye tv/mweb
        "extractor_args": {
            "youtube": {
                "player_client": ["tv", "mweb", "web"],  # tv sabse pehle
                "player_skip": ["webpage", "configs"],
                "skip": ["hls", "dash"],
            },
            "youtubetab": {"skip": ["authcheck"]},
        },
        
        # 🎯 LAYER 2: Cookies — real user authentication
        "cookiefile": cookiefile or "/tmp/melody_yt_cookies.txt",
        
        # 🎯 LAYER 4: JS Runtime — n-challenge solve karne ke liye
        "js_runtimes": {},
        "remote_components": ["ejs:github"],
        
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        },
    }
    
    # Node.js detect kar — n-challenge ke liye mandatory
    for rt in ("node", "bun", "deno"):
        if shutil.which(rt):
            opts["js_runtimes"] = {rt: {}}
            LOGGER.info(f"✅ JS runtime: {rt}")
            break
    else:
        LOGGER.warning("⚠️ No JS runtime — n-challenge fail hoga")
    
    return opts


async def fast_youtube(video_id: str) -> str | None:
    """YouTube se direct audio URL nikalo — 2s max timeout."""
    from yt_dlp import YoutubeDL
    # ✅ Short timeout: YouTube block hai to jaldi fail, JioSaavn ko jeetne do
    try:
        opts = build_yt_opts()
        opts["socket_timeout"] = 4
        opts["retries"] = 1
        opts["extractor_retries"] = 1
        opts["fragment_retries"] = 1
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://youtube.com/watch?v={video_id}", download=False)
            for fmt in info.get("formats", []):
                if fmt.get("acodec") != "none" and fmt.get("vcodec") == "none":
                    if fmt.get("url"):
                        LOGGER.info(f"⚡ YouTube direct: {video_id}")
                        return fmt["url"]
            if info.get("url"):
                return info["url"]
    except Exception as e:
        LOGGER.debug(f"YouTube fast failed: {e}")
    return None


async def _jiosaavn_verified(query, yt_title=None):
    """JioSaavn with title verification."""
    # Try self-contained first
    try:
        r = await _jiosaavn_direct(query or yt_title, yt_title)
        if r:
            return r
    except Exception as e:
        LOGGER.debug(f"Self-sav failed: {e}")

    try:
        from melody.core import alt_source as alt
        search_fn = stream_fn = None
        for n in ['jiosaavn_search', '_jiosaavn_search']:
            if hasattr(alt, n): search_fn = getattr(alt, n); break
        for n in ['jiosaavn_stream', 'jiosaavn_get_stream', 'jiosaavn_resolve']:
            if hasattr(alt, n): stream_fn = getattr(alt, n); break
        if not search_fn or not stream_fn: return None
        
        # 🎯 MULTI-QUERY: raw + title + cleaned title
        queries_to_try = []
        if query: queries_to_try.append(query)
        if yt_title: queries_to_try.append(yt_title)
        cleaned = _clean_title(yt_title or query or "")
        if cleaned: queries_to_try.append(cleaned)
        # Dedupe
        seen = set()
        unique_q = []
        for q in queries_to_try:
            ql = q.lower().strip()
            if ql and ql not in seen:
                seen.add(ql)
                unique_q.append(q)
        
        results = None
        used_query = ""
        for q in unique_q:
            try:
                results = await search_fn(q, limit=3)
                if results:
                    used_query = q
                    break
            except Exception:
                continue
        if not results: return None
        LOGGER.debug(f"JioSaavn query used: {used_query!r}")
        if yt_title:
            best, best_score = None, 0
            for r in results:
                t = r.get('title', '') if isinstance(r, dict) else getattr(r, 'title', str(r))
                score = SequenceMatcher(None, yt_title.lower(), t.lower()).ratio()
                if score > best_score: best_score, best = score, r
            if best_score < 0.55:
                LOGGER.warning(f"JioSaavn skip ({best_score:.2f})")
                return None
            arg = best.get('id') if isinstance(best, dict) else best
        else:
            arg = results[0].get('id') if isinstance(results[0], dict) else results[0]
        r = await stream_fn(arg)
        return r if isinstance(r, str) else (r.get('url') if isinstance(r, dict) else None)
    except Exception:
        return None


async def race_fastest(query: str, video_id: str, yt_title: str = None, timeout: float = 6.0, want_video: bool = False):
    # PRIORITY: JioSaavn first when YouTube is blocked
    try:
        from melody.core import alt_source as _alt
        _yt_blocked = _alt.youtube_blocked()
    except Exception:
        _yt_blocked = False
    
    if _yt_blocked:
        # Skip YouTube entirely — go straight to alt sources
        from melody.core import alt_source as alt
        for fn_name in ['jiosaavn_search', 'jiosaavn_resolve']:
            if hasattr(alt, fn_name):
                try:
                    r = await getattr(alt, fn_name)(query)
                    if isinstance(r, str) and r.startswith('http'):
                        LOGGER.info("⚡ JioSaavn (fast-path, YT blocked)")
                        return r, "JioSaavn"
                except Exception:
                    pass
    
    """Parallel race — YouTube + JioSaavn + SoundCloud. Jo pehle, wahi."""
    # ═══ YOUTUBE SKIP: SABR wall hai, JioSaavn ONLY ═══
    tasks = {
        "JioSaavn": asyncio.create_task(_jiosaavn_verified(query, yt_title)),
    }
    # SoundCloud parallel backup
    try:
        from melody.core import alt_source as _alt
        for _fn in ("soundcloud_resolve", "_soundcloud_resolve"):
            if hasattr(_alt, _fn):
                tasks["SoundCloud"] = asyncio.create_task(getattr(_alt, _fn)(query))
                break
    except Exception:
        pass
    # ✅ YouTube ke liye Piped/Invidious use karo — video ID se exact match, no bot-check
    tasks["YouTube"] = asyncio.create_task(youtube_via_api(video_id))
    tasks["JioSaavn"] = asyncio.create_task(_jiosaavn_verified(query, yt_title))
    # Last-resort for non-music videos (interviews, podcasts, etc.)
    tasks["Piped"] = asyncio.create_task(piped_last_resort(video_id))
    try:
        from melody.core import alt_source as alt
        for n in ['soundcloud_resolve', '_soundcloud_resolve']:
            if hasattr(alt, n):
                tasks["SoundCloud"] = asyncio.create_task(getattr(alt, n)(query))
                break
    except Exception:
        pass
    
    try:
        done, pending = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED, timeout=timeout)
        while done or pending:
            for t in list(done):
                try:
                    r = t.result()
                    if r and isinstance(r, str) and r.startswith("http"):
                        for p in pending: p.cancel()
                        for name, task in tasks.items():
                            if task is t:
                                LOGGER.info(f"⚡ Race winner: {name}")
                                return r, name
                except Exception:
                    pass
                done.discard(t)
            if not pending:
                break
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED, timeout=3)
    finally:
        for t in tasks.values():
            if not t.done(): t.cancel()
    return None, None


# ============================================================
# PIPED / INVIDIOUS RESOLVER (YouTube video ID se exact audio)
# ============================================================
PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.ducks.party",
    "https://api.piped.private.coffee",
]

INVIDIOUS_INSTANCES = [
    "https://yewtu.be",
    "https://invidious.f5.si",
    "https://inv.vern.cc",
    "https://invidious.nerdvpn.de",
]

async def piped_stream(video_id: str):
    import aiohttp, asyncio
    async def try_one(inst):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{inst}/streams/{video_id}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200: return None
                    d = await r.json()
                    for f in d.get("audioStreams", []):
                        u = f.get("url")
                        if u and isinstance(u, str) and u.startswith("http"):
                            return u
        except Exception:
            return None
    results = await asyncio.gather(*[try_one(i) for i in PIPED_INSTANCES], return_exceptions=True)
    for r in results:
        if isinstance(r, str) and r.startswith("http"):
            return r
    return None


async def invidious_stream(video_id: str):
    import aiohttp, asyncio
    async def try_one(inst):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{inst}/api/v1/videos/{video_id}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200: return None
                    d = await r.json()
                    for f in d.get("adaptiveFormats", []):
                        if str(f.get("type","")).startswith("audio"):
                            u = f.get("url")
                            if u and isinstance(u, str) and u.startswith("http"):
                                return u
        except Exception:
            return None
    results = await asyncio.gather(*[try_one(i) for i in INVIDIOUS_INSTANCES], return_exceptions=True)
    for r in results:
        if isinstance(r, str) and r.startswith("http"):
            return r
    return None


async def youtube_via_api(video_id: str):
    import asyncio
    tasks = [
        asyncio.create_task(piped_stream(video_id)),
        asyncio.create_task(invidious_stream(video_id)),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED, timeout=6)
        for t in done:
            try:
                r = t.result()
                if r and isinstance(r, str) and r.startswith("http"):
                    for p in pending: p.cancel()
                    return r
            except Exception:
                pass
    finally:
        for t in tasks:
            if not t.done(): t.cancel()
    return None


# ═══════════════════════════════════════════════════════════════
# PIPED / INVIDIOUS — Last-resort YouTube fallback (non-music videos)
# ═══════════════════════════════════════════════════════════════
_PIPED = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.ducks.party",
    "https://api.piped.private.coffee",
]
_INV = [
    "https://yewtu.be",
    "https://invidious.f5.si",
    "https://inv.vern.cc",
    "https://invidious.nerdvpn.de",
]

async def piped_last_resort(video_id: str):
    """Piped + Invidious race — non-music videos ke liye."""
    import aiohttp, asyncio
    async def p(inst):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{inst}/streams/{video_id}",
                                 timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200: return None
                    d = await r.json()
                    for f in d.get("audioStreams", []):
                        u = f.get("url")
                        if u and isinstance(u, str) and u.startswith("http"):
                            return u
        except Exception: return None
        return None
    async def i(inst):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{inst}/api/v1/videos/{video_id}",
                                 timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200: return None
                    d = await r.json()
                    for f in d.get("adaptiveFormats", []):
                        if str(f.get("type","")).startswith("audio"):
                            u = f.get("url")
                            if u and isinstance(u, str) and u.startswith("http"):
                                return u
        except Exception: return None
        return None
    tasks = [asyncio.create_task(p(x)) for x in _PIPED] + \
            [asyncio.create_task(i(x)) for x in _INV]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED, timeout=8)
        while done or pending:
            for t in list(done):
                try:
                    r = t.result()
                    if r and isinstance(r, str) and r.startswith("http"):
                        for pp in pending: pp.cancel()
                        return r
                except Exception: pass
                done.discard(t)
            if not pending: break
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED, timeout=3)
    finally:
        for t in tasks:
            if not t.done(): t.cancel()
    return None



# ═══════════════════════════════════════════════════════════════
# SELF-CONTAINED JIOSAAVN (no alt_source dependency)
# ═══════════════════════════════════════════════════════════════
import aiohttp as _aiohttp_sav

_SAV_BASES = [
    "https://saavn.dev/api",
    "https://jiosaavn-api-privatecvc2.vercel.app/api",
    "https://jiosaavn-api.vercel.app/api",
]


async def _jiosaavn_direct(query: str, expected_title: str = None):
    """Search JioSaavn + return direct audio URL (320kbps preferred)."""
    if not query:
        return None
    for base in _SAV_BASES:
        try:
            async with _aiohttp_sav.ClientSession() as s:
                async with s.get(f"{base}/search/songs",
                                 params={"query": query, "limit": 5},
                                 timeout=_aiohttp_sav.ClientTimeout(total=6)) as r:
                    if r.status != 200:
                        continue
                    d = await r.json()
            results = (d.get("data") or {}).get("results") or []
            if not results:
                continue
            best, best_score = None, 0
            try:
                from melody.core.alt_source import version_mismatch as _vm
            except Exception:  # noqa: BLE001
                _vm = lambda a, b: False
            for item in results:
                t = (item.get("name") or "").lower()
                if expected_title and _vm(expected_title, t):
                    continue  # karaoke/cover/remix != original
                if expected_title:
                    score = SequenceMatcher(None, expected_title.lower(), t).ratio()
                    if score > best_score:
                        best_score, best = score, item
                else:
                    best = item
                    break
            if expected_title and best_score < 0.6:
                continue
            if not best:
                continue
            for u in (best.get("downloadUrl") or []):
                if u.get("quality") in ("320kbps", "160kbps", "96kbps"):
                    link = u.get("link") or u.get("url")
                    if link:
                        LOGGER.info(f"✅ JioSaavn: {best.get('name')[:50]}")
                        return link
        except Exception:
            continue
    return None

