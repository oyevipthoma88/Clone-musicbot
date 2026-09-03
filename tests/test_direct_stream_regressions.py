from contextlib import contextmanager
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_direct_resolver_rotates_client_profiles_before_giving_up():
    """A transient/default client block must not force a full download."""
    from melody.core import ytdl

    calls = []
    formats = [{
        "url": "https://cdn.example/audio.webm?expire=4102444800",
        "protocol": "https",
        "vcodec": "none",
        "acodec": "opus",
        "abr": 96,
    }]

    class FakeYDL:
        def __init__(self, profile):
            self.profile = profile

        def extract_info(self, target, download=False):
            calls.append(self.profile)
            if self.profile in (None, ["default"]):
                raise RuntimeError("default client blocked")
            return {"formats": formats, "http_headers": {"User-Agent": "test"}}

    @contextmanager
    def fake_locked(opts):
        profile = ((opts.get("extractor_args") or {}).get("youtube") or {}).get("player_client")
        yield FakeYDL(profile)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ytdl, "_ydl_opts", lambda audio_only=True: {
        "format": "bestaudio",
        "extractor_args": {"youtube": {"player_client": ["default"]}},
    })
    monkeypatch.setattr(ytdl, "_locked_ytdl", fake_locked)
    try:
        result = ytdl._resolve_stream_urls_sync("https://www.youtube.com/watch?v=iAIBF2ngbWY", False)
    finally:
        monkeypatch.undo()

    assert result["audio"] == formats[0]["url"]
    assert calls[0] == ["default"]
    assert calls[1] == ["ios", "android_vr"]


def test_direct_resolver_keeps_hls_as_a_valid_last_resort():
    from melody.core import ytdl

    source = (ROOT / "melody/core/ytdl.py").read_text(encoding="utf-8")
    assert "direct_profiles = (" in source
    assert '["tv_simply", "tv"]' in source
    assert "hlsManifestUrl" in source
    assert "download fallback engaged" in (ROOT / "melody/core/call.py").read_text(encoding="utf-8")
