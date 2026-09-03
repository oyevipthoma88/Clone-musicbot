from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_group_audit_has_name_id_count_and_permission_fields():
    source = (ROOT / "melody/logging.py").read_text(encoding="utf-8")
    for marker in (
        "def _audit_person",
        "async def log_group_event",
        "Members at event time",
        "By / actor",
        "Target:",
        "Permission snapshot",
        "old_status",
        "new_status",
    ):
        assert marker in source


def test_membership_audit_covers_assistant_and_admin_transitions():
    source = (ROOT / "melody/plugins/misc/group_audit.py").read_text(encoding="utf-8")
    for marker in (
        "on_chat_member_updated",
        "assistant_banned",
        "assistant_removed",
        "assistant_promoted",
        "assistant_demoted",
        "assistant_joined",
        "from_user",
        "old_status",
        "new_status",
    ):
        assert marker in source


def test_bot_added_uses_rich_audit_logger():
    source = (ROOT / "melody/plugins/misc/start.py").read_text(encoding="utf-8")
    assert "log_group_event(" in source
    assert '"bot_added_to_group"' in source
