"""
🔁 /reboot + /reload — like every top music bot.

REQUESTED ("/reload, /reboot cmnd add kr baki music bot ka dekh ke"):

  /reload   (group, admins)   → refresh this chat's admin list + auth list +
                                settings cache. Use it right after promoting
                                someone so the bot sees them instantly.
  /reload   (owner, private)  → additionally hot-reloads every plugin module
                                and re-warms the assistant's peer cache.
  /reboot   (sudo / owner)    → full process restart, works in groups too.
  /restart                    → alias handled in restart.py (private only).

Both commands answer FIRST and do the slow work afterwards, so the user
never stares at a dead chat.
"""
import asyncio
import importlib
import os
import pkgutil
import sys

from pyrogram import Client, enums, filters
from pyrogram.types import Message

from melody import bot
from melody.config import Config
from melody.logging import LOGGER
from utils.decorators import error_handler, owner_only, refresh_chat_admins
from utils.database import invalidate_auth_cache, get_auth_users
from utils.formatters import quote_html
from utils.gc_db import is_sudo


# ── /reload ─────────────────────────────────────────────────────────────────

@bot.on_message(filters.command(["reload", "admincache", "refresh"]) & filters.group)
@error_handler
async def reload_group_cmd(client: Client, message: Message):
    """Refresh only THIS chat's caches for an owner/sudo operator."""
    user = message.from_user
    if not user or not await is_sudo(user.id):
        return  # hidden administrative command
    chat_id = message.chat.id
    msg = await message.reply(
        quote_html("🔄 <b>Rᴇʟᴏᴀᴅɪɴɢ ᴀᴅᴍɪɴ ᴄᴀᴄʜᴇ…</b>"),
        parse_mode=enums.ParseMode.HTML,
    )

    admins = await refresh_chat_admins(client, chat_id)

    invalidate_auth_cache(chat_id)
    try:
        auth = await get_auth_users(chat_id)
    except Exception:
        auth = []

    try:
        from utils.gc_db import invalidate_flag_cache

        invalidate_flag_cache(chat_id)
    except Exception:
        pass

    await msg.edit(
        quote_html(
            "<blockquote>✅ <b>Rᴇʟᴏᴀᴅᴇᴅ</b></blockquote>\n\n"
            f"👮 <b>Admins:</b> <code>{len(admins)}</code>\n"
            f"🔑 <b>Auth users:</b> <code>{len(auth)}</code>\n"
            "⚙️ <b>Settings cache:</b> cleared\n\n"
            "<i>Naye admins ab turant recognise honge.</i>"
        ),
        parse_mode=enums.ParseMode.HTML,
    )


@bot.on_message(filters.command(["reload", "admincache"]) & filters.private)
@owner_only
@error_handler
async def reload_cmd(client: Client, message: Message):
    """Hot-reload all plugins; full plugin reload is owner-DM only."""
    msg = await message.reply(
        quote_html("🔄 <b>Reloading plugins…</b>"), parse_mode=enums.ParseMode.HTML
    )
    reloaded = []
    failed = []

    import melody.plugins as plugins_pkg

    for finder, name, ispkg in pkgutil.walk_packages(
        plugins_pkg.__path__, plugins_pkg.__name__ + "."
    ):
        try:
            mod = sys.modules.get(name)
            if mod:
                importlib.reload(mod)
                reloaded.append(name.split(".")[-1])
            else:
                importlib.import_module(name)
                reloaded.append(name.split(".")[-1])
        except Exception as e:
            LOGGER.error("Reload failed for %s: %s", name, e)
            failed.append(name.split(".")[-1])

    # Also refresh the assistant's peer cache — a stale/empty peer table is
    # what makes the first /play after a restart fail with "ID not found".
    warmed = 0
    try:
        from melody.core.call import warm_assistant_peers

        warmed = await warm_assistant_peers()
    except Exception as e:
        LOGGER.warning("Peer warm-up during /reload failed: %s", e)

    # Drop every cached permission/auth entry so promotions apply at once.
    try:
        from utils.decorators import invalidate_admin_cache

        invalidate_admin_cache()
        invalidate_auth_cache()
    except Exception:
        pass

    text = f"✅ <b>Reloaded {len(reloaded)} plugins</b> · 🎙 {warmed} VC peers warmed\n"
    if reloaded:
        text += f"<code>{'</code>, <code>'.join(reloaded[:20])}</code>\n"
    if failed:
        text += f"\n❌ <b>Failed ({len(failed)}):</b> <code>{'</code>, <code>'.join(failed)}</code>"

    await msg.edit(quote_html(text), parse_mode=enums.ParseMode.HTML)


# ── /reboot ─────────────────────────────────────────────────────────────────

async def _do_reboot():
    await asyncio.sleep(1)
    os.execv(sys.executable, [sys.executable, "-m", "melody"])


async def _reboot_chat(chat_id: int) -> None:
    """Reset playback and chat-local caches without touching other groups."""
    from melody.core.call import stop_stream
    from melody.core.autoplay import reset_autoplay_guard
    await stop_stream(chat_id)
    reset_autoplay_guard(chat_id)
    invalidate_auth_cache(chat_id)
    try:
        from utils.decorators import invalidate_admin_cache
        invalidate_admin_cache(chat_id)
    except Exception:
        pass
    try:
        from utils.gc_db import invalidate_flag_cache
        invalidate_flag_cache(chat_id)
    except Exception:
        pass


@bot.on_message(filters.command(["reboot"]))
@error_handler
async def reboot_cmd(client: Client, message: Message):
    """Group: restart only that chat. Private: full reboot for owner only."""
    user = message.from_user
    if not user:
        return
    is_private = bool(getattr(message.chat, "type", None) == enums.ChatType.PRIVATE)
    if is_private:
        # A sudo user must never be able to restart the whole worker.
        if user.id != Config.OWNER_ID:
            return
        await message.reply(
            quote_html(
                "<blockquote>🔁 <b>Rᴇʙᴏᴏᴛɪɴɢ 𝑨𝒑𝒆𝒙 𝑽𝒊𝒃𝒆𝒔…</b></blockquote>\n"
                "<i>Kuch hi seconds mein wapas online.</i>"
            ),
            parse_mode=enums.ParseMode.HTML,
        )
        LOGGER.info("Full reboot requested by owner %s via private DM", user.id)
        await _do_reboot()
        return

    if getattr(message.chat, "type", None) not in {
        enums.ChatType.GROUP, enums.ChatType.SUPERGROUP,
    } or not await is_sudo(user.id):
        return

    chat_id = message.chat.id
    await message.reply(
        quote_html(
            "<blockquote>♻️ <b>Gᴄ ʀᴇʙᴏᴏᴛ ᴄᴏᴍᴘʟᴇᴛᴇ</b></blockquote>\n"
            "<i>Sirf isi group ka playback/session reset hua hai. Baaki groups unaffected hain.</i>"
        ),
        parse_mode=enums.ParseMode.HTML,
    )
    LOGGER.info("Chat-local reboot requested by sudo/owner %s in %s", user.id, chat_id)
    await _reboot_chat(chat_id)
