import re

# ============================================================
# FIX 1: FORCE WEBM/OPUS (Overwrites entire fmt block)
# WebM ka header START me hota hai, isliye download hote-hi baj jata hai.
# ============================================================
with open("melody/core/ytdl.py", "r", encoding="utf-8") as f:
    content = f.read()

lines = content.split('\n')
start_idx = -1
end_idx = -1
for i, line in enumerate(lines):
    if 'fmt = (' in line and start_idx == -1:
        start_idx = i
    if start_idx != -1 and 'if audio_only' in line:
        end_idx = i
        break

if start_idx != -1 and end_idx != -1:
    new_lines = lines[:start_idx] + [
        '    fmt = (',
        '        "bestaudio[ext=webm]/bestaudio[ext=opus]/bestaudio[ext=ogg]/"',
        '        "bestaudio[protocol=m3u8]/bestaudio[protocol=m3u8_native]/"',
        '        "bestaudio[ext=m4a][protocol*=dash]/bestaudio[format_id=140]/"',
        '        "bestaudio/best"',
        '        if audio_only'
    ] + lines[end_idx+1:]
    with open("melody/core/ytdl.py", "w", encoding="utf-8") as f:
        f.write('\n'.join(new_lines))
    print("✅ FIX 1: WebM/Opus format strictly forced.")
else:
    print("❌ FIX 1: Could not find fmt block.")

# ============================================================
# FIX 2: SKIP FFPROBE ON .part FILES (Prevents TimeoutError)
# ============================================================
with open("utils/pytgcalls_patch.py", "r", encoding="utf-8") as f:
    probe_content = f.read()

old_probe = '''        if not _ffprobe_available():
            return None'''

new_probe = '''        if not _ffprobe_available():
            return None
        # SPEED JUGAAD: Skip ffprobe for growing .part files to prevent infinite hang
        if str(path).endswith(".part") or ".part-" in str(path) or ".ytdl" in str(path):
            log.info("⚡ Early-handoff partial file — skipping ffprobe.")
            return None'''

if old_probe in probe_content:
    probe_content = probe_content.replace(old_probe, new_probe)
    with open("utils/pytgcalls_patch.py", "w", encoding="utf-8") as f:
        f.write(probe_content)
    print("✅ FIX 2: ffprobe skip for .part files applied.")
else:
    print("⚠️ FIX 2: ffprobe pattern not found (may already be patched).")

