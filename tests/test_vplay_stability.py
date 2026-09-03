from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_youtube_vplay_uses_completed_file_by_default():
    source = (ROOT / "melody/core/call.py").read_text(encoding="utf-8")
    assert 'os.getenv("DIRECT_VIDEO_STREAM", "false")' in source
    assert "and (not video or _DIRECT_VIDEO_STREAM)" in source
    assert "both\n            # py-tgcalls pipelines then read one immutable local container" in source


def test_audio_and_live_direct_paths_remain_available():
    source = (ROOT / "melody/core/call.py").read_text(encoding="utf-8")
    assert "direct_first = live_source or (" in source
    assert "should_try_direct_stream()" in source
    assert "if not live_source:" in source


def test_manual_play_entrypoints_reset_speed_at_core_boundary():
    source = (ROOT / "melody/core/call.py").read_text(encoding="utf-8")
    assert "# Manual force-play is also a fresh user request" in source
    assert "# Keep the invariant at the core boundary" in source
    assert source.count("reset_playback_speed(chat_id)") >= 3


def test_failed_speed_swap_restores_previous_speed():
    source = (ROOT / "melody/core/call.py").read_text(encoding="utf-8")
    assert "previous_speed = get_speed(chat_id)" in source
    assert "half-applied 2x/0.5x value" in source
    assert "_speed[chat_id] = previous_speed" in source


def test_video_download_does_not_use_growing_file_handoff():
    source = (ROOT / "melody/core/call.py").read_text(encoding="utf-8")
    assert "allow_early=not video" in source
    assert "and (not video or _DIRECT_VIDEO_STREAM)" in source


def test_expected_innertube_fallback_noise_is_debug_only():
    source = (ROOT / "melody/core/ytdl.py").read_text(encoding="utf-8")
    assert 'LOGGER.debug("InnerTube search: all client contexts failed' in source
    assert 'LOGGER.debug("InnerTube related: all client contexts failed' in source
    assert 'LOGGER.debug(\n                    "InnerTube %s next HTTP' in source
