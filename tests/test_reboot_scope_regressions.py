from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_reload_group_requires_sudo_and_keeps_chat_scope():
    source = (ROOT / "melody/plugins/owner/reboot.py").read_text(encoding="utf-8")
    assert "filters.command([\"reload\", \"admincache\", \"refresh\"]) & filters.group" in source
    assert "if not user or not await is_sudo(user.id):" in source
    assert "refresh_chat_admins(client, chat_id)" in source
    assert "invalidate_auth_cache(chat_id)" in source
    assert "invalidate_flag_cache(chat_id)" in source


def test_private_reload_is_owner_only_full_plugin_reload():
    source = (ROOT / "melody/plugins/owner/reboot.py").read_text(encoding="utf-8")
    assert "filters.command([\"reload\", \"admincache\"]) & filters.private" in source
    assert "@owner_only" in source
    assert "importlib.reload(mod)" in source
    assert "warm_assistant_peers()" in source


def test_reboot_separates_owner_dm_from_group_local_scope():
    source = (ROOT / "melody/plugins/owner/reboot.py").read_text(encoding="utf-8")
    assert "is_private = bool" in source
    assert "if user.id != Config.OWNER_ID:" in source
    assert "await _do_reboot()" in source
    assert "async def _reboot_chat(chat_id: int)" in source
    assert "await stop_stream(chat_id)" in source
    assert "Chat-local reboot requested" in source


def test_group_reboot_does_not_call_process_reboot():
    source = (ROOT / "melody/plugins/owner/reboot.py").read_text(encoding="utf-8")
    group_branch = source.split("if getattr(message.chat, \"type\", None) not in", 1)[1]
    assert "await _do_reboot()" not in group_branch
