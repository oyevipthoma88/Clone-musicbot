"""Detailed group membership and bot/assistant lifecycle audit logging."""
from __future__ import annotations

from melody import assistant, bot
from melody.logging import log_group_event
from utils.tasks import spawn


def _status(member) -> str:
    return str(getattr(member, "status", "unknown") or "unknown").lower()


def _rights(member) -> str:
    if not member:
        return ""
    names = (
        "can_manage_chat", "can_delete_messages", "can_restrict_members",
        "can_invite_users", "can_pin_messages", "can_promote_members",
        "can_manage_video_chats",
    )
    enabled = [name.removeprefix("can_") for name in names if getattr(member, name, False)]
    return ", ".join(enabled) if enabled else "no elevated rights"


@bot.on_chat_member_updated()
async def group_membership_audit(client, update):
    """Log every membership/status transition with human-readable identities."""
    chat = getattr(update, "chat", None)
    old = getattr(update, "old_chat_member", None)
    new = getattr(update, "new_chat_member", None)
    target = getattr(new, "user", None) or getattr(old, "user", None)
    if not chat or not target:
        return

    old_status = _status(old)
    new_status = _status(new)
    actor = getattr(update, "from_user", None)
    if old_status == new_status and _rights(old) == _rights(new):
        return

    try:
        me = await client.get_me()
        assistant_me = await assistant.get_me() if assistant is not None else None
    except Exception:
        me = None
        assistant_me = None

    tracked = {u.id for u in (me, assistant_me) if u is not None}
    is_tracked = getattr(target, "id", None) in tracked
    action = "member_status_changed"
    if new_status in {"kicked", "banned"}:
        action = "assistant_banned" if assistant_me and target.id == assistant_me.id else "member_banned"
    elif new_status in {"left", "restricted"} and old_status in {"member", "administrator", "owner"}:
        action = "assistant_removed" if assistant_me and target.id == assistant_me.id else "member_removed"
    elif new_status in {"administrator", "owner"}:
        action = "assistant_promoted" if assistant_me and target.id == assistant_me.id else "member_promoted"
    elif old_status in {"administrator", "owner"} and new_status == "member":
        action = "assistant_demoted" if assistant_me and target.id == assistant_me.id else "member_demoted"
    elif new_status == "member" and old_status in {"left", "kicked", "banned"}:
        action = "assistant_joined" if assistant_me and target.id == assistant_me.id else "member_joined"

    # Ordinary member churn is useful but can be extremely noisy. Always keep
    # assistant/bot lifecycle events; retain all admin/status changes.
    if not is_tracked and action in {"member_status_changed", "member_joined", "member_removed"}:
        return

    details = f"New rights: {_rights(new)}"
    spawn(log_group_event(
        client, action, chat.id,
        actor=actor, target=target, assistant=assistant_me,
        old_status=old_status, new_status=new_status,
        result="status update received", details=details,
    ), name=f"audit-{chat.id}-{getattr(target, 'id', 'unknown')}")
