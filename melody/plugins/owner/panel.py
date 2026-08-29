"""Apex Vibes owner panel: only safe runtime/status controls."""
import time
import psutil
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from melody import bot
from melody.config import Config
from melody.core.call import _active
from melody.core.queue import get_current
from utils.decorators import owner_only, error_handler
from utils.database import get_all_chats, get_stats

_started_at = time.time()


def _panel_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Bot status", callback_data="apex_owner_status")],
        [InlineKeyboardButton("📡 Active voice chats", callback_data="apex_owner_activevc")],
    ])


def _back_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↩ Back", callback_data="apex_owner_panel")],
    ])


@bot.on_message(filters.command("panel") & filters.private)
@owner_only
@error_handler
async def owner_panel(client: Client, message: Message):
    await message.reply(
        "<blockquote>👑 <b>𝑨𝒑𝒆𝒙 𝑽𝒊𝒃𝒆𝒔 .ᐟ.ᐟ Owner Panel</b></blockquote>\n\n"
        "Music runtime controls and voice-chat status only.",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=_panel_markup(),
    )


@bot.on_callback_query(filters.regex(r"^apex_owner_panel$"))
@error_handler
async def panel_home(client: Client, query: CallbackQuery):
    if query.from_user.id != Config.OWNER_ID:
        return await query.answer("Owner only", show_alert=True)
    await query.message.edit_text(
        "<blockquote>👑 <b>𝑨𝒑𝒆𝒙 𝑽𝒊𝒃𝒆𝒔 .ᐟ.ᐟ Owner Panel</b></blockquote>\n\n"
        "Music runtime controls and voice-chat status only.",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=_panel_markup(),
    )
    await query.answer()


@bot.on_callback_query(filters.regex(r"^apex_owner_status$"))
@error_handler
async def owner_status(client: Client, query: CallbackQuery):
    if query.from_user.id != Config.OWNER_ID:
        return await query.answer("Owner only", show_alert=True)
    try:
        stats = await get_stats()
    except Exception:
        stats = {"chats": 0}
    uptime = int(time.time() - _started_at)
    ram = psutil.virtual_memory()
    text = (
        "<b>𝑨𝒑𝒆𝒙 𝑽𝒊𝒃𝒆𝒔 .ᐟ.ᐟ Status</b>\n\n"
        f"Uptime: <code>{uptime // 3600}h {(uptime % 3600) // 60}m</code>\n"
        f"RAM: <code>{ram.percent}%</code>\n"
        f"Served chats: <code>{stats.get('chats', 0)}</code>"
    )
    await query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=_back_markup())
    await query.answer()


@bot.on_callback_query(filters.regex(r"^apex_owner_activevc$"))
@error_handler
async def owner_active_vcs(client: Client, query: CallbackQuery):
    if query.from_user.id != Config.OWNER_ID:
        return await query.answer("Owner only", show_alert=True)
    active = [chat_id for chat_id, state in _active.items() if state]
    if not active:
        text = "<b>Active voice chats</b>\n\nNo active voice chats."
    else:
        rows = []
        for chat_id in active:
            track = get_current(chat_id)
            title = getattr(track, "title", "Nothing queued") if track else "Nothing queued"
            rows.append(f"<code>{chat_id}</code> — {title[:50]}")
        text = "<b>Active voice chats</b>\n\n" + "\n".join(rows)
    await query.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=_back_markup())
    await query.answer()
