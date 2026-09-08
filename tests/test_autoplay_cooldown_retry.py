from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_autoplay_cooldown_is_retryable_without_leaving_voice_chat():
    autoplay = (ROOT / "melody/core/autoplay.py").read_text(encoding="utf-8")
    call = (ROOT / "melody/core/call.py").read_text(encoding="utf-8")

    assert "def autoplay_retry_after(chat_id: int) -> float" in autoplay
    assert "_retry_autoplay_after_cooldown" in call
    assert "keeping voice chat and retrying" in call
    assert "if retry_after > 0 and chat_id not in _autoplay_retry_scheduled:" in call

    # The cooldown branch must return before the hard-leave block. This guards
    # against regressing to the old behavior where AutoPlay gave up and left.
    cooldown = call.index("if retry_after > 0 and chat_id not in _autoplay_retry_scheduled:")
    leave = call.index("_mark_leaving(chat_id)", cooldown)
    assert cooldown < leave
