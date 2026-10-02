"""Regression tests for the non-YouTube audio fallback (Oct 2 bot-wall log)."""
import importlib.util
import logging
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_alt_source():
    # Load the module in isolation (no pyrogram / Mongo needed).
    for name, attrs in (
        ("melody", {"__path__": [str(ROOT / "melody")]}),
        ("melody.core", {"__path__": [str(ROOT / "melody/core")]}),
    ):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(mod, k, v)
            sys.modules[name] = mod
    if "melody.logging" not in sys.modules:
        lg = types.ModuleType("melody.logging")
        lg.LOGGER = logging.getLogger("test")
        sys.modules["melody.logging"] = lg
    spec = importlib.util.spec_from_file_location(
        "melody.core.alt_source_under_test", ROOT / "melody/core/alt_source.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_clean_title_strips_youtube_noise():
    a = _load_alt_source()
    assert a.clean_title("Gal Karke (Official Video) | Asees Kaur | T-Series") == "Gal Karke Asees Kaur"
    assert a.clean_title("Tum Hi Ho - Aashiqui 2 | Full Song | Arijit Singh") == "Tum Hi Ho Aashiqui 2"


def test_matching_never_accepts_a_different_song():
    a = _load_alt_source()
    # Same duration but unrelated title -> rejected.
    assert not a._good_enough(a._score("Gal Karke Asees Kaur", "Kesariya Arijit Singh", 157, 157), 157)
    # Right title, 1h jukebox -> rejected.
    assert not a._good_enough(a._score("Gal Karke Asees Kaur", "Gal Karke Asees Kaur", 157, 3600), 157)
    # Right title and duration -> accepted.
    assert a._good_enough(a._score("Gal Karke Asees Kaur", "Gal Karke Asees Kaur", 157, 157), 157)


def test_breaker_prefers_fallback_after_bot_wall():
    a = _load_alt_source()
    a.clear_youtube_block()
    assert not a.youtube_blocked()
    a.mark_youtube_blocked("test")
    assert a.youtube_blocked()
    a.clear_youtube_block()


def test_download_path_uses_alt_source_after_youtube_failure():
    source = (ROOT / "melody/core/ytdl.py").read_text(encoding="utf-8")
    assert "from melody.core import alt_source as _alt_source" in source
    assert "_alt_source.youtube_blocked()" in source
    assert "_alt_source.mark_youtube_blocked(" in source
    # The alt fallback must run even for errors the permanent gate rejects,
    # because a flagged IP disguises the block as "Video unavailable".
    locked = source[source.index("async def _download_audio_locked("):]
    locked = locked[: locked.index("raise dl_error")]
    tail = locked[locked.index("if rescued:"):]
    assert "_is_permanent_download_error" not in tail
    assert "_alt_source_download(" in tail


if __name__ == "__main__":
    test_clean_title_strips_youtube_noise()
    test_matching_never_accepts_a_different_song()
    test_breaker_prefers_fallback_after_bot_wall()
    test_download_path_uses_alt_source_after_youtube_failure()
    print("alt source fallback tests: PASS")
