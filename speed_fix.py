import os, re

def patch(filepath, old, new):
    if not os.path.exists(filepath):
        print(f"❌ File missing: {filepath}")
        return False
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    if old in content:
        content = content.replace(old, new)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✅ Patched: {filepath}")
        return True
    else:
        print(f"⚠️ Pattern not found in {filepath} (may already be patched or changed)")
        return False

# ============================================================
# FIX 1: Skip direct-stream resolution entirely.
# It keeps failing with ValueError and wastes ~2 seconds.
# We force the code to go STRAIGHT to download fallback.
# ============================================================
# Find the direct stream attempt in _build_direct_stream and make it return None instantly
patch(
    "melody/core/call.py",
    '''        from melody.core.ytdl import resolve_stream_urls''',
    '''        from melody.core.ytdl import resolve_stream_urls
        # SPEED-JUGAD: Direct CDN stream keeps failing (ValueError) on this host
        # and burns ~2s before falling back anyway. Skip it entirely -> download fast-path.
        if os.getenv("FORCE_DOWNLOAD_FIRST", "1") == "1":
            raise ValueError("forced download-first fast path")'''
)

# ============================================================
# FIX 2: Lower early-handoff threshold so playback starts
# as soon as a small prefix is on disk (not after full download).
# ============================================================
patch(
    "melody/core/ytdl.py",
    '''_EARLY_HANDOFF_BYTES = _env_int("EARLY_HANDOFF_BYTES", 16_000)''',
    '''_EARLY_HANDOFF_BYTES = _env_int("EARLY_HANDOFF_BYTES", 60_000)  # SPEED: 60KB prefix is enough for WebM/Opus header+audio'''
)

patch(
    "melody/core/ytdl.py",
    '''_EARLY_HANDOFF_LARGE_FILE_PREFIX = _env_int("EARLY_HANDOFF_LARGE_FILE_PREFIX", 16_000)  # ⚡ SPEED: 128KB→64KB''',
    '''_EARLY_HANDOFF_LARGE_FILE_PREFIX = _env_int("EARLY_HANDOFF_LARGE_FILE_PREFIX", 80_000)  # SPEED: 80KB prefix'''
)

# ============================================================
# FIX 3: Force WebM/Opus as the PREFERRED download format.
# WebM has its header at the START of the file, so ffmpeg can
# play it progressively while yt-dlp is still downloading.
# (M4A keeps the moov atom at the END -> can't early-play.)
# ============================================================
patch(
    "melody/core/ytdl.py",
    '''    fmt = (
        "bestaudio[protocol=m3u8]/bestaudio[protocol=m3u8_native]/"
        f"bestaudio[ext=webm][abr<={_env_int('YT_AUDIO_MAX_ABR', 48)}]/"
        "bestaudio[ext=webm]/"
        "bestaudio[ext=opus]/bestaudio[abr<=128]/"
        "bestaudio[ext=ogg]/bestaudio[abr<=128]/best"
        if audio_only''',
    '''    fmt = (
        "bestaudio[ext=webm]/bestaudio[ext=opus]/"
        "bestaudio[protocol=m3u8]/bestaudio[protocol=m3u8_native]/"
        f"bestaudio[ext=webm][abr<={_env_int('YT_AUDIO_MAX_ABR', 48)}]/"
        "bestaudio[ext=ogg]/bestaudio[abr<=128]/best"
        if audio_only'''
)

# ============================================================
# FIX 4: Make early-handoff accept WebM/Opus prefix reliably.
# Ensure the growing .part file is treated as playable as soon
# as the threshold bytes land, so we don't wait for completion.
# ============================================================
patch(
    "melody/core/ytdl.py",
    '''_EARLY_AUDIO_STREAMABLE_EXTS = {"webm", "ogg", "oga", "opus", "mp3", "flac", "wav"}''',
    '''_EARLY_AUDIO_STREAMABLE_EXTS = {"webm", "ogg", "oga", "opus", "mp3", "flac", "wav", "mka"}'''
)

print("\n🎯 All speed patches applied.")
