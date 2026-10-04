"""
Cloudflare Worker YouTube proxy — clean IP for resolve + download.
Worker accepts ?url=<encoded-url> and forwards to YouTube-related hosts.
"""
import os, re, json, logging
from urllib.parse import quote, unquote

LOGGER = logging.getLogger(__name__)
WORKER = os.getenv("YT_WORKER_URL", "https://yt-proxy.flirtingzero.workers.dev").strip().rstrip("/")


def wrap(url: str) -> str:
    if not url or not WORKER:
        return url
    if WORKER in url:
        return url
    return f"{WORKER}/?url={quote(url, safe='')}"


def unwrap(url: str) -> str:
    if not url or WORKER not in url:
        return url
    m = re.search(r"[?&]url=([^&]+)", url)
    return unquote(m.group(1)) if m else url


def _parse_player(html: str):
    """Extract ytInitialPlayerResponse JSON from watch page HTML."""
    for pat in [
        r"ytInitialPlayerResponse\s*=\s*(\{.+?\})\s*;\s*(?:var|const|let|</script>)",
        r'window\["ytInitialPlayerResponse"\]\s*=\s*(\{.+?\})\s*;',
        r'"ytInitialPlayerResponse"\s*:\s*(\{.+?\})\s*,"',
    ]:
        m = re.search(pat, html, re.DOTALL)
        if not m:
            continue
        try:
            return json.loads(m.group(1))
        except Exception:
            raw, depth, end = m.group(1), 0, -1
            for i, c in enumerate(raw):
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end > 0:
                try:
                    return json.loads(raw[:end])
                except Exception:
                    continue
    return None


async def resolve(video_id: str):
    """Fetch watch page via Worker, extract direct audio URL (wrapped)."""
    if not WORKER:
        return None
    try:
        import curl_cffi.requests as _cfr
        page = f"https://www.youtube.com/watch?v={video_id}&has_verified=1&bpctr=9999999999"
        wrapped = wrap(page)
        async with _cfr.AsyncSession() as s:
            r = await s.get(
                wrapped,
                impersonate="chrome120",
                timeout=20,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
        if r.status_code != 200:
            LOGGER.debug(f"Worker: HTTP {r.status_code} for {video_id}")
            return None
        pr = _parse_player(r.text)
        if not pr:
            LOGGER.debug(f"Worker: no player response for {video_id}")
            return None
        status = (pr.get("playabilityStatus") or {}).get("status", "")
        if status and status not in ("OK", "PLAYABLE"):
            LOGGER.debug(f"Worker: not playable ({status})")
            return None
        sd = pr.get("streamingData") or {}
        formats = (sd.get("adaptiveFormats") or []) + (sd.get("formats") or [])
        audios = []
        for f in formats:
            mime = f.get("mimeType", "")
            if not mime.startswith("audio/"):
                continue
            u = f.get("url")
            if not u:
                continue
            audios.append({
                "url": u,
                "itag": f.get("itag"),
                "bitrate": f.get("bitrate", 0),
                "mime": mime,
            })
        if not audios:
            LOGGER.debug(f"Worker: no direct audio URLs for {video_id}")
            return None
        audios.sort(key=lambda x: x["bitrate"] or 0, reverse=True)
        # Prefer 64-160 kbps for faster download, else top
        best = next(
            (a for a in audios if 64000 <= (a["bitrate"] or 0) <= 160000),
            audios[0],
        )
        LOGGER.info(
            f"⚡ Worker resolved {video_id}: itag={best['itag']} "
            f"br={best['bitrate']} mime={best['mime'][:20]}"
        )
        return wrap(best["url"])
    except Exception as e:
        LOGGER.debug(f"Worker resolve failed: {e}")
        return None


def info() -> dict:
    return {"url": WORKER, "enabled": bool(WORKER)}
