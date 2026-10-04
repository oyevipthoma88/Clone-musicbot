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
    """Extract ytInitialPlayerResponse JSON from watch page HTML.

    Uses brace-balanced extraction (handles `}` inside strings that break
    naive regex on modern YouTube HTML).
    """
    for marker in (
        "ytInitialPlayerResponse = ",
        'window["ytInitialPlayerResponse"] = ',
        "ytInitialPlayerResponse=",
    ):
        idx = html.find(marker)
        if idx == -1:
            continue
        start = html.find("{", idx)
        if start == -1:
            continue
        # Brace-balanced extraction
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(html)):
            c = html[i]
            if esc:
                esc = False
                continue
            if c == "\\":
                esc = True
                continue
            if c == '"' and not esc:
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    raw = html[start:i+1]
                    try:
                        return json.loads(raw)
                    except Exception as e:
                        LOGGER.info(f"Worker parse JSON error: {e}")
                        return None
    return None




async def resolve(video_id: str):
    """Fetch watch page via Worker, extract direct audio URL (wrapped)."""
    if not WORKER:
        LOGGER.warning("Worker: URL not configured")
        return None
    try:
        import curl_cffi.requests as _cfr
        page = f"https://www.youtube.com/watch?v={video_id}&has_verified=1&bpctr=9999999999999"
        wrapped = wrap(page)
        LOGGER.info(f"Worker: fetching {wrapped[:90]}...")
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
        LOGGER.info(f"Worker: HTTP {r.status_code}, {len(r.text)} bytes")
        if r.status_code != 200:
            return None
        # Quick check: is it a real watch page?
        if "ytInitialPlayerResponse" not in r.text:
            LOGGER.warning(f"Worker: no ytInitialPlayerResponse marker (len={len(r.text)})")
            LOGGER.info(f"Worker: HTML preview: {r.text[:200]}")
            return None
        pr = _parse_player(r.text)
        if not pr:
            LOGGER.warning("Worker: ytInitialPlayerResponse parse failed")
            return None
        LOGGER.info(f"Worker: parsed player response OK")
        status = (pr.get("playabilityStatus") or {}).get("status", "")
        reason = (pr.get("playabilityStatus") or {}).get("reason", "")
        LOGGER.info(f"Worker: playabilityStatus={status} reason={reason!r}")
        if status and status not in ("OK", "PLAYABLE"):
            LOGGER.warning(f"Worker: not playable ({status})")
            return None
        sd = pr.get("streamingData") or {}
        adaptive = sd.get("adaptiveFormats") or []
        regular = sd.get("formats") or []
        LOGGER.info(f"Worker: adaptiveFormats={len(adaptive)} formats={len(regular)}")
        audios = []
        for f in adaptive + regular:
            mime = f.get("mimeType", "")
            if not mime.startswith("audio/"):
                continue
            u = f.get("url")
            if not u:
                # SABR / signatureCipher — no direct URL
                continue
            audios.append({
                "url": u,
                "itag": f.get("itag"),
                "bitrate": f.get("bitrate", 0),
                "mime": mime,
            })
        LOGGER.info(f"Worker: {len(audios)} audio(s) with direct URL")
        if not audios:
            # Log what we got instead
            sample = (adaptive + regular)[:3]
            for s_ in sample:
                LOGGER.info(f"Worker: sample format: mime={s_.get('mimeType')} itag={s_.get('itag')} url={'Y' if s_.get('url') else 'N'} cipher={'Y' if s_.get('signatureCipher') else 'N'}")
            return None
        audios.sort(key=lambda x: x["bitrate"] or 0, reverse=True)
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
        LOGGER.warning(f"Worker resolve exception: {e}")
        return None

def info() -> dict:
    return {"url": WORKER, "enabled": bool(WORKER)}
