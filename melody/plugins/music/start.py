"""Minimal Apex Vibes start/help responses for a music-only bot."""
from pyrogram import Client, filters
from pyrogram.types import Message
from melody import bot
from melody.config import Config
from utils.decorators import error_handler

# The full profile already provides richer /start and /help handlers from
# misc.start. Keep this compact module active only for MUSIC_ONLY_MODE, where
# the misc package is intentionally not loaded; otherwise two handlers answer
# the same command and users receive duplicate replies.
_music_only = filters.create(
    lambda _client, _update: bool(getattr(Config, "MUSIC_ONLY_MODE", False))
)


_START_TEXT = (
    "<blockquote><b>𝑨𝒑𝒆𝒙 𝑽𝒊𝒃𝒆𝒔 .ᐟ.ᐟ</b></blockquote>\n\n"
    "YouTube music and voice-chat live support is online.\n\n"
    "Use <code>/play song name</code> in a group voice chat.\n"
    "Use <code>/panel</code> for the owner panel."
)

_HELP_TEXT = (
    "<b>𝑨𝒑𝒆𝒙 𝑽𝒊𝒃𝒆𝒔 .ᐟ.ᐟ</b>\n\n"
    "Music: <code>/play</code>, <code>/pause</code>, <code>/resume</code>, "
    "<code>/skip</code>, <code>/stop</code>, <code>/queue</code>, <code>/np</code>\n"
    "Controls: <code>/volume</code>, <code>/mute</code>, <code>/loop</code>, "
    "<code>/shuffle</code>, <code>/seek</code>, <code>/speed</code>\n"
    "Voice chat: <code>/joinvc</code>, <code>/leavevc</code>, <code>/autoend</code>"
)


@bot.on_message(filters.command("start") & filters.private & _music_only)
@error_handler
async def start_command(client: Client, message: Message):
    await message.reply(_START_TEXT)


@bot.on_message(filters.command("help") & _music_only)
@error_handler
async def help_command(client: Client, message: Message):
    await message.reply(_HELP_TEXT)
