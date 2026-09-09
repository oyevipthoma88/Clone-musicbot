from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_custom_emoji_verification_has_transient_failure_backoff():
    source = (ROOT / "utils/custom_emoji.py").read_text(encoding="utf-8")
    assert "_RESOLUTION_RETRY_AT = 0.0" in source
    assert "_RESOLUTION_RETRY_COOLDOWN = 60.0" in source
    assert "temporarily backing off after a timeout" in source
    assert "backing off for %.0fs" in source
