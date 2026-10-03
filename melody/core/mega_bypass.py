"""
MEGA BYPASS — Top 5 Heroku music bots ka combined logic:
- LyriFusionBot: 9-layer stack
- VenomMusic: PO Token + WARP proxy
- AshokShau: COOKIES_URL auto-refresh
- PsychochauffeurBot: Remote API + local fallback
- Telegramusic: Cookies auto-reload
"""
import os, socket, shutil, logging, time
import aiohttp
from urllib.parse import urlparse

LOGGER = logging.getLogger(__name__)

# ============================================================
# LAYER 1: COOKIES AUTO-REFRESH (AshokShau + Telegramusic style)
# ============================================================
COOKIES_URL = os.getenv("COOKIES_URL", "").strip()
COOKIES_PATH = "/tmp/melody_yt_cookies.txt"
_cookies_last_fetch = 0.0
_COOKIES_REFRESH_INTERVAL = 3600  # 1 hour

async def refresh_cookies_from_url() -> bool:
    """GitHub Gist/Pastebin se cookies fetch karo — redeploy ki zaroorat nahi."""
    global _cookies_last_fetch
    if not COOKIES_URL:
        return False
    now = time.time()
    if (now - _cookies_last_fetch) < _COOKIES_REFRESH_INTERVAL:
        return True
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(COOKIES_URL, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    text = await r.text()
                    if "# Netscape HTTP Cookie File" in text or "# HTTP Cookie File" in text:
                        with open(COOKIES_PATH, "w") as f:
                            f.write(text)
                        _cookies_last_fetch = now
                        LOGGER.info(f"🍪 Cookies refreshed from COOKIES_URL")
                        return True
    except Exception as e:
        LOGGER.warning(f"Cookies refresh failed: {e}")
    return False

# ============================================================
# LAYER 2: PROXY REACHABILITY (VenomMusic style)
# ============================================================
_PROXY_CACHE = {"ok": None, "at": 0.0}
_TTL = 30.0

def proxy_reachable(timeout: float = 1.5) -> bool:
    """Proxy sirf tab use karo jab actually reachable ho."""
    import time as _t
    proxy = os.getenv("YTDLP_PROXY", "").strip()
    if not proxy:
        return False
    now = _t.time()
    if _PROXY_CACHE["ok"] is not None and (now - _PROXY_CACHE["at"]) < _TTL:
        return _PROXY_CACHE["ok"]
    try:
        parsed = urlparse(proxy if "//" in proxy else f"//{proxy}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (1080 if parsed.scheme == "socks5" else 8086)
        with socket.create_connection((host, port), timeout=timeout):
            _PROXY_CACHE.update({"ok": True, "at": now})
            return True
    except Exception:
        _PROXY_CACHE.update({"ok": False, "at": now})
        return False

# ============================================================
# LAYER 3: JS RUNTIME DETECTION (LyriFusion style)
# ============================================================
def detect_js_runtime() -> str | None:
    """Node.js/bun/deno — n-challenge solve karne ke liye."""
    for rt in ("node", "bun", "deno"):
        if shutil.which(rt):
            LOGGER.info(f"✅ JS runtime: {rt}")
            return rt
    return None

# ============================================================
# LAYER 4: COMBINED BYPASS — Sabko yt-dlp opts me inject karo
# ============================================================
def apply_mega_bypass(opts: dict) -> dict:
    """Top 5 bots ka combined logic — 5 layers ek saath."""

    # Layer 1: Proxy (only if reachable)
    proxy = os.getenv("YTDLP_PROXY", "").strip()
    if proxy:
        if proxy_reachable():
            opts["proxy"] = proxy
            LOGGER.info(f"✅ Proxy active: {proxy}")
        else:
            opts.pop("proxy", None)
            LOGGER.info("⚠️ Proxy down — direct connection")

    # Layer 2: Multi-client fallback (LyriFusion + VenomMusic)
    ea = opts.setdefault("extractor_args", {})
    yt = ea.setdefault("youtube", {})
    yt["player_client"] = ["tv", "mweb", "web", "ios"]
    yt.setdefault("player_skip", ["webpage", "configs"])
    yt.setdefault("skip", ["hls", "dash"])

    # Layer 3: JS runtime
    rt = detect_js_runtime()
    if rt:
        opts["js_runtimes"] = {rt: {}}
        opts["remote_components"] = ["ejs:github"]

    # Layer 4: Aggressive retry (PsychochauffeurBot style)
    opts.setdefault("retries", 5)
    opts.setdefault("fragment_retries", 5)
    opts.setdefault("extractor_retries", 3)
    opts.setdefault("concurrent_fragment_downloads", 5)
    opts.setdefault("http_chunk_size", 10 * 1024 * 1024)
    opts.setdefault("socket_timeout", 15)
    opts.setdefault("geo_bypass", True)

    # Layer 5: Cookie path
    if os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH

    return opts

# ============================================================
# LAYER 5: PARALLEL SOURCE RACE (Sab bots ka common pattern)
# ============================================================
async def race_all_sources(query: str, video_id: str, yt_title: str = None, timeout: float = 8.0):
    """JioSaavn + SoundCloud + Invidious — jo pehle, wahi stream."""
    tasks = {
        "JioSaavn": asyncio.create_task(_race_jiosaavn(query, yt_title)),
        "SoundCloud": asyncio.create_task(_race_soundcloud(query)),
        "Invidious": asyncio.create_task(_race_invidious(video_id)),
    }
    try:
        done, pending = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED, timeout=timeout)
        for name, task in tasks.items():
            if task in done:
                try:
                    r = task.result()
                    if r and isinstance(r, str) and r.startswith("http"):
                        for p in pending: p.cancel()
                        LOGGER.info(f"⚡ Race winner: {name}")
                        return r, name
                except Exception: pass
        if pending:
            done2, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED, timeout=4)
            for name, task in tasks.items():
                if task in done2:
                    try:
                        r = task.result()
                        if r and isinstance(r, str) and r.startswith("http"):
                            for p in pending: p.cancel()
                            return r, name
                    except Exception: pass
    finally:
        for t in tasks.values():
            if not t.done(): t.cancel()
    return None, None

async def _race_jiosaavn(query, yt_title=None):
    try:
        from melody.core import alt_source as alt
        from difflib import SequenceMatcher
        search_fn = stream_fn = None
        for n in ['jiosaavn_search', '_jiosaavn_search', 'search_jiosaavn']:
            if hasattr(alt, n): search_fn = getattr(alt, n); break
        for n in ['jiosaavn_stream', 'jiosaavn_get_stream', '_jiosaavn_stream',
                  '_jiosaavn_get_stream', 'get_jiosaavn_stream', 'jiosaavn_resolve']:
            if hasattr(alt, n): stream_fn = getattr(alt, n); break
        if not search_fn or not stream_fn: return None
        results = await search_fn(query, limit=3)
        if not results: return None
        if yt_title:
            best, best_score = None, 0
            for r in results:
                t = r.get('title', '') if isinstance(r, dict) else getattr(r, 'title', str(r))
                score = SequenceMatcher(None, yt_title.lower(), t.lower()).ratio()
                if score > best_score: best_score, best = score, r
            if best_score < 0.55:
                LOGGER.warning(f"JioSaavn mismatch skip ({best_score:.2f})")
                return None
            arg = best.get('id') if isinstance(best, dict) else best
        else:
            arg = results[0].get('id') if isinstance(results[0], dict) else results[0]
        r = await stream_fn(arg)
        return r if isinstance(r, str) else (r.get('url') if isinstance(r, dict) else None)
    except Exception as e:
        LOGGER.debug(f"JioSaavn race: {e}")
        return None

async def _race_soundcloud(query):
    try:
        from melody.core import alt_source as alt
        for n in ['soundcloud_resolve', '_soundcloud_resolve', 'soundcloud_search',
                  '_soundcloud_search', 'search_soundcloud', 'soundcloud_get_stream']:
            if hasattr(alt, n):
                r = await getattr(alt, n)(query)
                if isinstance(r, str) and r.startswith('http'): return r
                if isinstance(r, dict): return r.get('url') or r.get('stream_url')
        return None
    except Exception as e:
        LOGGER.debug(f"SoundCloud race: {e}")
        return None

async def _race_invidious(video_id):
    instances = [
        "https://yewtu.be", "https://vid.puffyan.us",
        "https://invidious.flokinet.to", "https://invidious.nerdvpn.de",
        "https://inv.vern.cc", "https://invidious.f5.si",
    ]
    async def try_one(inst):
        try:
            async with aiohttp.ClientSession() as s:
                url = f"{inst}/api/v1/videos/{video_id}?fields=adaptiveFormats"
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200: return None
                    d = await r.json()
                    for f in d.get("adaptiveFormats", []):
                        if str(f.get("type", "")).startswith("audio"):
                            u = f.get("url")
                            if u and u.startswith('http'): return u
        except Exception: return None
    results = await asyncio.gather(*[try_one(i) for i in instances], return_exceptions=True)
    for r in results:
        if isinstance(r, str) and r.startswith('http'): return r
    return None

import asyncio
