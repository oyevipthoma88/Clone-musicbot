"""
YouTube Fast Bypass — Top 5 bots ka combined logic.
Layers: Multi-client (tv/mweb) + JS runtime + cookies + parallel race.
"""
import os, shutil, logging, asyncio
from difflib import SequenceMatcher

LOGGER = logging.getLogger(__name__)

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
    """YouTube se direct audio URL nikalo — fast."""
    from yt_dlp import YoutubeDL
    try:
        with YoutubeDL(build_yt_opts()) as ydl:
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


async def race_fastest(query: str, video_id: str, yt_title: str = None, timeout: float = 6.0):
    """Parallel race — YouTube + JioSaavn + SoundCloud. Jo pehle, wahi."""
    tasks = {
        "YouTube": asyncio.create_task(fast_youtube(video_id)),
        "JioSaavn": asyncio.create_task(_jiosaavn_verified(query, yt_title)),
    }
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
