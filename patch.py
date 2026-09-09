import os

def replace_in_file(filepath, old_code, new_code):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    if old_code in content:
        content = content.replace(old_code, new_code)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✅ Patched {filepath}")
    else:
        print(f"❌ Could not find target code in {filepath}")

old1 = '''def _early_audio_path_is_safe(path: str) -> bool:
    """Return True only for containers whose prefix is probeable/playable.

    yt-dlp may expose native-fragment paths as ``file.webm.part-Frag159``
    rather than the simpler ``file.webm.part``. Treat both as the underlying
    WebM/Opus container; otherwise a valid growing audio file is rejected and
    the caller waits for the complete download.
    """
    name = os.path.basename(path or "").lower()
    name = re.sub(r"\\.part(?:[-._][a-z0-9_-]+)?$", "", name)
    for suffix in (".ytdl", ".temp", ".part"):
        while name.endswith(suffix):
            name = name[: -len(suffix)]
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    return ext in _EARLY_AUDIO_STREAMABLE_EXTS'''

new1 = '''def _early_audio_path_is_safe(path: str) -> bool:
    """Return True only for containers whose prefix is probeable/playable."""
    name = os.path.basename(path or "").lower()
    name = re.sub(r"\\.part(?:[-._][a-z0-9_-]+)?$", "", name)
    for suffix in (".ytdl", ".temp", ".part"):
        while name.endswith(suffix):
            name = name[: -len(suffix)]
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    
    if ext in _EARLY_AUDIO_STREAMABLE_EXTS:
        return True
        
    # ROOT-FIX: Allow early handoff for fragmented mp4 (m4a/mp4) files.
    if ext in {"m4a", "mp4"}:
        try:
            with open(path, "rb") as f:
                header = f.read(8192)
            return b"moof" in header or b"mdat" in header or b"styp" in header
        except Exception:
            return False
            
    return False'''

replace_in_file("melody/core/ytdl.py", old1, new1)

old2 = '''    direct_profiles = (
        None,  # use the normal authenticated + PO-token policy first
        ["ios", "android_vr"],
        ["tv_simply", "tv"],
        ["web_safari", "web_embedded"],
        ["mweb"],
    )
    last_info: dict = {}
    last_exc: Exception | None = None
    for profile in direct_profiles:
        opts = dict(base_opts)
        if profile:
            extractor_args = {k: dict(v) for k, v in (base_opts.get("extractor_args") or {}).items()}
            youtube = dict(extractor_args.get("youtube") or {})
            youtube["player_client"] = list(profile)
            extractor_args["youtube"] = youtube
            opts["extractor_args"] = extractor_args
        # The format selector only matters for a download; picking the
        # streamable pair by hand needs the FULL format list.
        opts.pop("format", None)
        try:
            with _locked_ytdl(opts) as ydl:
                info = ydl.extract_info(target, download=False)
            if info and info.get("entries"):
                info = info["entries"][0]
            info = info or {}
            last_info = info
            picked = _pick_stream_formats(info, want_video)
            if not picked and info.get("url"):
                # Some extractor clients return one playable URL at top level
                # without a populated formats array (including HLS-only
                # responses, which ffmpeg can consume directly).
                proto = str(info.get("protocol") or "")
                top_url_path = str(info["url"]).split("?", 1)[0].lower()
                top_level_hls = "m3u8" in proto or top_url_path.endswith(".m3u8")
                top_level_http = proto.startswith("http") and "dash" not in proto
                if top_level_http or top_level_hls:
                    picked = ({"video": info["url"], "audio": info["url"]}
                              if want_video else {"audio": info["url"], "video": None})
            if picked:
                break
        except Exception as exc:  # try the next independent client profile
            last_exc = exc
            LOGGER.debug("direct yt-dlp profile %s failed for %s: %s", profile or "default", target, exc)
            picked = {}
    else:
        picked = {}

    if not picked:
        safe_target_id = _extract_video_id(target) or "unknown"
        formats = last_info.get("formats") or []
        LOGGER.info(
            "#stream direct formats unavailable for %s: formats=%d http=%d hls=%d audio=%d muxed=%d video=%d profiles=%d",
            safe_target_id, len(formats),
            sum(1 for f in formats if str(f.get("protocol") or "").startswith("http")),
            sum(1 for f in formats if "m3u8" in str(f.get("protocol") or "") or str(f.get("url") or "").split("?")[0].endswith(".m3u8")),
            sum(1 for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")),
            sum(1 for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") not in (None, "none")),
            sum(1 for f in formats if f.get("vcodec") not in (None, "none")),
            len(direct_profiles),
        )
        raise last_exc or ValueError("no directly streamable http format found")'''

new2 = '''    last_info: dict = {}
    last_exc: Exception | None = None
    picked = {}
    
    # Walk the download ladder to find a streamable URL. The ladder contains
    # the exact client/format combinations that bypass YouTube's bot protection.
    for index, step in enumerate(_DOWNLOAD_LADDER):
        opts = _apply_ladder_step(base_opts, step, not want_video)
        # We are only resolving for direct streaming, so don't apply postprocessors
        opts.pop("postprocessors", None)
        
        try:
            with _locked_ytdl(opts) as ydl:
                info = ydl.extract_info(target, download=False)
            if info and info.get("entries"):
                info = info["entries"][0]
            info = info or {}
            last_info = info
            picked = _pick_stream_formats(info, want_video)
            if not picked and info.get("url"):
                proto = str(info.get("protocol") or "")
                top_url_path = str(info["url"]).split("?", 1)[0].lower()
                top_level_hls = "m3u8" in proto or top_url_path.endswith(".m3u8")
                top_level_http = proto.startswith("http") and "dash" not in proto
                if top_level_http or top_level_hls:
                    picked = ({"video": info["url"], "audio": info["url"]}
                              if want_video else {"audio": info["url"], "video": None})
            if picked:
                break
        except Exception as exc:
            last_exc = exc
            LOGGER.debug("direct yt-dlp ladder step %d failed for %s: %s", index, target, exc)
            picked = {}
            
    if not picked:
        safe_target_id = _extract_video_id(target) or "unknown"
        formats = last_info.get("formats") or []
        LOGGER.info(
            "#stream direct formats unavailable for %s: formats=%d http=%d hls=%d audio=%d muxed=%d video=%d ladder_steps=%d",
            safe_target_id, len(formats),
            sum(1 for f in formats if str(f.get("protocol") or "").startswith("http")),
            sum(1 for f in formats if "m3u8" in str(f.get("protocol") or "") or str(f.get("url") or "").split("?")[0].endswith(".m3u8")),
            sum(1 for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")),
            sum(1 for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") not in (None, "none")),
            sum(1 for f in formats if f.get("vcodec") not in (None, "none")),
            len(_DOWNLOAD_LADDER),
        )
        raise last_exc or ValueError("no directly streamable http format found")'''

replace_in_file("melody/core/ytdl.py", old2, new2)

old3 = '''            # ⚡ SPEED FIX: Simplified retry logic - try cache first, then force retry
            try:
                urls = await resolve_stream_urls(
                    track.video_id, want_video=video, force=False,
                )
            except Exception as cached_exc:
                # First attempt failed - retry with force=True to bypass negative cache
                LOGGER.info(
                    "#stream direct source failed for %s (%s) — retrying with force=True",
                    track.video_id, type(cached_exc).__name__,
                )
                try:
                    urls = await resolve_stream_urls(
                        track.video_id, want_video=video, force=True,
                    )
                except Exception as retry_exc:
                    # Both attempts failed - fall back to download
                    LOGGER.info(
                        "#stream direct source unavailable for %s after retry (%s) — using download fallback",
                        track.video_id, type(retry_exc).__name__,
                    )
                    return None'''

new3 = '''            # SPEED FIX: Try resolve once. If it fails, it caches the failure for 60s.
            # Retrying immediately with force=True would walk the ladder again, taking
            # another 1.5-5s and exceeding the 5-10s strict startup limit.
            # Fall back to the download immediately, which now supports early handoff
            # for m4a/mp4 (fMP4) and starts playing in <2 seconds!
            try:
                urls = await resolve_stream_urls(
                    track.video_id, want_video=video, force=False,
                )
            except Exception as cached_exc:
                LOGGER.info(
                    "#stream direct source unavailable for %s (%s) — using download fallback",
                    track.video_id, type(cached_exc).__name__,
                )
                return None'''

replace_in_file("melody/core/call.py", old3, new3)
