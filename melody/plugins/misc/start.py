"""Apex Vibes start menu: music, VC live chat, and Owner Panel only."""
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from melody import bot
from melody.config import Config
from utils.decorators import error_handler


def _menu(owner: bool = False):
    rows = [
        [InlineKeyboardButton("▶️ Play Music", switch_inline_query_current_chat=""),
         InlineKeyboardButton("🎛 Music Controls", callback_data="apex_music_help")],
        [InlineKeyboardButton("🎧 Voice Chat", callback_data="apex_vc_help")],
    ]
    if owner:
        rows.append([InlineKeyboardButton("👑 Owner Panel", callback_data="owner_panel")])
    return InlineKeyboardMarkup(rows)


_START = (
    "<blockquote><b>𝑨𝒑𝒆𝒙 𝑽𝒊𝒃𝒆𝒔 .ᐟ.ᐟ</b></blockquote>\n\n"
    "YouTube music and voice-chat live support is online.\n\n"
    "Use <code>/play song name</code> in a group voice chat."
)

_HELP = (
    "<b>𝑨𝒑𝒆𝒙 𝑽𝒊𝒃𝒆𝒔 .ᐟ.ᐟ Music</b>\n\n"
    "<code>/play</code> <code>/vplay</code> <code>/pause</code> <code>/resume</code> "
    "<code>/skip</code> <code>/stop</code> <code>/queue</code> <code>/np</code>\n"
    "<code>/volume</code> <code>/mute</code> <code>/loop</code> <code>/shuffle</code> "
    "<code>/seek</code> <code>/speed</code> <code>/playlist</code>\n"
    "<code>/live</code> <code>/radio</code> <code>/joinvc</code> <code>/leavevc</code>"
)

_VC_HELP = (
    "<b>𝑨𝒑𝒆𝒙 𝑽𝒊𝒃𝒆𝒔 .ᐟ.ᐟ Voice Chat</b>\n\n"
    "<code>/joinvc</code> — Join the group voice chat\n"
    "<code>/leavevc</code> — Leave the voice chat\n"
    "<code>/autoend on|off</code> — Stop when VC is empty\n"
    "<code>/vcnotify on|off</code> — Toggle live VC notifications"
)


def _back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩ Back", callback_data="apex_start_home")]])


@bot.on_message(filters.command("start") & filters.private)
@error_handler
async def start_dm(client: Client, message: Message):
    await message.reply(_START, parse_mode=enums.ParseMode.HTML,
                        reply_markup=_menu(bool(message.from_user and message.from_user.id == Config.OWNER_ID)))


@bot.on_message(filters.command("start") & filters.group)
@error_handler
async def start_group(client: Client, message: Message):
    await message.reply(_START, parse_mode=enums.ParseMode.HTML, reply_markup=_menu(False))


@bot.on_message(filters.command("help"))
@error_handler
async def help_command(client: Client, message: Message):
    await message.reply(_HELP, parse_mode=enums.ParseMode.HTML, reply_markup=_menu(False))


@bot.on_callback_query(filters.regex(r"^apex_music_help$"))
async def music_help(client: Client, query: CallbackQuery):
    await query.message.edit_text(_HELP, parse_mode=enums.ParseMode.HTML, reply_markup=_back())
    await query.answer()


@bot.on_callback_query(filters.regex(r"^apex_vc_help$"))
async def vc_help(client: Client, query: CallbackQuery):
    await query.message.edit_text(_VC_HELP, parse_mode=enums.ParseMode.HTML, reply_markup=_back())
    await query.answer()


@bot.on_callback_query(filters.regex(r"^apex_start_home$"))
async def start_home(client: Client, query: CallbackQuery):
    await query.message.edit_text(_START, parse_mode=enums.ParseMode.HTML,
                                  reply_markup=_menu(bool(query.from_user and query.from_user.id == Config.OWNER_ID)))
    await query.answer()
