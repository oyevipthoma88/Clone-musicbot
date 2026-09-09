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
        print(f"❌ Target code not found in {filepath} (May have been updated or modified)")

# FIX 1: Force DASH/Fragmented M4A in yt-dlp format selector
old1 = '''    fmt = (
        "bestaudio[protocol=m3u8]/bestaudio[protocol=m3u8_native]/"
        f"bestaudio[ext=webm][abr<={_env_int('YT_AUDIO_MAX_ABR', 48)}]/"
        "bestaudio[ext=webm]/"
        "bestaudio[ext=opus]/bestaudio[abr<=128]/"
        "bestaudio[ext=ogg]/bestaudio[abr<=128]/best"
        if audio_only'''

new1 = '''    fmt = (
        "bestaudio[protocol=m3u8]/bestaudio[protocol=m3u8_native]/"
        f"bestaudio[ext=webm][abr<={_env_int('YT_AUDIO_MAX_ABR', 48)}]/"
        "bestaudio[ext=webm]/"
        "bestaudio[ext=opus]/bestaudio[abr<=128]/"
        "bestaudio[ext=ogg]/bestaudio[abr<=128]/"
        "bestaudio[ext=m4a][protocol*=dash]/"
        "bestaudio[format_id=140]/bestaudio[format_id=139]/"
        "bestaudio[format_id=251]/bestaudio[format_id=250]/bestaudio[format_id=249]/"
        "best"
        if audio_only'''

replace_in_file("melody/core/ytdl.py", old1, new1)

# FIX 2: Strictly reject progressive M4A in early handoff (Root Fix for TimeoutError)
old2 = '''    # ROOT-FIX: Allow early handoff for fragmented mp4 (m4a/mp4) files.
    if ext in {"m4a", "mp4"}:
        try:
            with open(path, "rb") as f:
                header = f.read(8192)
            return b"moof" in header or b"mdat" in header or b"styp" in header
        except Exception:
            return False'''

new2 = '''    # ROOT-FIX: Allow early handoff ONLY for strictly fragmented mp4 (m4a/mp4).
    # Progressive M4A has 'mdat' at the start but 'moov' at the EOF.
    # ffprobe hangs waiting for EOF on growing progressive M4A, causing TimeoutError!
    # Fragmented M4A has 'styp' or 'moof' boxes at the start, which ffprobe probes instantly.
    if ext in {"m4a", "mp4"}:
        try:
            with open(path, "rb") as f:
                header = f.read(8192)
            return b"styp" in header or b"moof" in header
        except Exception:
            return False'''

replace_in_file("melody/core/ytdl.py", old2, new2)

# FIX 3: Add DASH priority to Download Ladder
old3 = '''    {"_client": ["tv_simply", "tv"], "_format": "bestaudio[ext=webm][protocol^=http]/bestaudio[ext=opus][protocol^=http]/bestaudio[ext=ogg][protocol^=http]/bestaudio[protocol^=http]/bestaudio/best",
     "concurrent_fragment_downloads": 1, "_no_merge": True},
    {"_client": ["web_safari", "web_embedded"],
     "_format": "bestaudio[ext=webm][protocol^=http]/bestaudio[ext=opus][protocol^=http]/bestaudio[ext=ogg][protocol^=http]/bestaudio[protocol^=http]/bestaudio/best", "_no_merge": True},'''

new3 = '''    {"_client": ["tv_simply", "tv"], "_format": "bestaudio[ext=webm][protocol^=http]/bestaudio[ext=opus][protocol^=http]/bestaudio[ext=ogg][protocol^=http]/bestaudio[ext=m4a][protocol*=dash]/bestaudio[format_id=140]/bestaudio[protocol^=http]/bestaudio/best",
     "concurrent_fragment_downloads": 1, "_no_merge": True},
    {"_client": ["web_safari", "web_embedded"],
     "_format": "bestaudio[ext=webm][protocol^=http]/bestaudio[ext=opus][protocol^=http]/bestaudio[ext=ogg][protocol^=http]/bestaudio[ext=m4a][protocol*=dash]/bestaudio[format_id=140]/bestaudio[protocol^=http]/bestaudio/best", "_no_merge": True},'''

replace_in_file("melody/core/ytdl.py", old3, new3)

# FIX 4: Stop pytgcalls_patch from hanging infinitely on second probe
old4 = '''            if not _is_local_playable(path) and await _wait_for_growth(path):
                try:
                    return await original(
                        ffmpeg_parameters, path, stream_parameters, *args, **kwargs
                    )
                except Exception as exc:'''

new4 = '''            if not _is_local_playable(path) and await _wait_for_growth(path):
                try:
                    return await _asyncio.wait_for(
                        original(
                            ffmpeg_parameters, path, stream_parameters, *args, **kwargs
                        ),
                        timeout=PROBE_TIMEOUT_LOCAL,
                    )
                except Exception as exc:'''

replace_in_file("utils/pytgcalls_patch.py", old4, new4)
