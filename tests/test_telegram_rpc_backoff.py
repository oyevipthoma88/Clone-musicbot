from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_custom_emoji_resolution_is_single_flight():
    source = (ROOT / "utils/custom_emoji.py").read_text(encoding="utf-8")
    assert "_RESOLUTION_LOCK = asyncio.Lock()" in source
    assert "async with _RESOLUTION_LOCK:" in source
    assert "_resolve_custom_emoji_unlocked" in source


def test_vc_lookup_backs_off_telegram_rpc_failures():
    source = (ROOT / "melody/core/vc_notify.py").read_text(encoding="utf-8")
    assert "_call_retry_at: dict = {}" in source
    assert "_CALL_RPC_BACKOFF = 30.0" in source
    assert 'if "RPC_CALL_FAIL" in str(exc).upper():' in source
    assert "_call_retry_at[chat_id] = time.monotonic() + _CALL_RPC_BACKOFF" in source
