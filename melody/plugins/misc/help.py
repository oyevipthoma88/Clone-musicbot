"""Apex Vibes music and voice-chat help menu."""
from pyrogram import Client, filters, enums
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from melody import bot

_MUSIC_HELP = (
    "<b>𝑨𝒑𝒆𝒙 𝑽𝒊𝒃𝒆𝒔 .ᐟ.ᐟ Music</b>\n\n"
    "<b>Playback</b>\n"
    "<code>/play</code> <code>/vplay</code> <code>/playforce</code> <code>/vplayforce</code>\n"
    "<code>/pause</code> <code>/resume</code> <code>/skip</code> <code>/stop</code>\n"
    "<code>/queue</code> <code>/np</code> <code>/remove</code> <code>/clearqueue</code>\n\n"
    "<b>Controls</b>\n"
    "<code>/volume</code> <code>/mute</code> <code>/unmute</code> <code>/loop</code>\n"
    "<code>/loopall</code> <code>/noloop</code> <code>/shuffle</code>\n"
    "<code>/seek</code> <code>/seekback</code> <code>/speed</code> <code>/playmode</code>\n\n"
    "<b>Streams and playlists</b>\n"
    "<code>/live</code> <code>/radio</code> <code>/replay</code> <code>/playlist</code>\n"
    "<code>/addplaylist</code> <code>/myplaylist</code> <code>/playplaylist</code>\n\n"
    "<b>Voice chat</b>\n"
    "<code>/joinvc</code> <code>/leavevc</code> <code>/autoend</code> <code>/vcnotify</code>"
)


def _markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Play Music", switch_inline_query_current_chat="")],
        [InlineKeyboardButton("🎛 Playback Controls", callback_data="apex_help_controls")],
    ])


@bot.on_callback_query(filters.regex(r"^apex_help_controls$"))
async def controls_help(client: Client, query: CallbackQuery):
    await query.message.edit_text(_MUSIC_HELP, parse_mode=enums.ParseMode.HTML, reply_markup=_markup())
    await query.answer()
