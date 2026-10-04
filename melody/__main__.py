"""
🎶 Apex Vibes Bot — Entry point
FIXES:
  - Plugins loaded BEFORE bot.start() so handlers register correctly
  - Slash commands registered via set_my_commands() on startup
  - Detailed log channel message with plugin count and system info
  - Peer id invalid patched: updates from unseen peers are silently dropped
    on startup instead of crashing the asyncio Task with an unhandled
    ValueError (see _patch_pyrogram_peer_errors docstring).
"""
import asyncio
import importlib
import pkgutil
import platform
import uvloop
from melody.logging import LOGGER
from melody.core.mega_bypass import refresh_cookies_from_url
from melody.config import Config
from utils.database import get_all_chats
from utils.tasks import spawn


def _patch_pyrogram_peer_errors():
    """Monkey-patch Pyrogram's handle_updates to suppress startup peer errors.

    ROOT-CAUSE FIX for:
        asyncio Task exception was never retrieved
        ValueError: Peer id invalid: -1002453972770

    WHY IT HAPPENS
    ══════════════
    Heroku wipes the dyno filesystem on every restart, so Pyrogram's SQLite
    peer cache is empty.  As soon as `assistant.start()` returns, Pyrogram
    starts dispatching buffered Telegram updates.  If any update carries a
    channel or group ID that isn't in the peer cache yet, Pyrogram's internal
    `handle_updates()` calls `resolve_peer(channel_id)`:
      1. `storage.get_peer_by_id(peer_id)` → KeyError (cache miss)
      2. Fallback: `utils.get_peer_type(peer_id)` → ValueError: Peer id invalid
    The exception propagates out of the Task and becomes an unhandled
    "Task exception was never retrieved" log line.  It is completely harmless
    (no data is lost; Telegram will re-deliver the update), but it looks like
    a crash and can obscure real errors in logs.

    Our `warm_peer_cache(assistant)` call fixes the cache 8-10 seconds later,
    but updates that arrive in that window still trigger the error.

    THE FIX
    ═══════
    Wrap the original `Client.handle_updates` method so that any ValueError
    whose message contains "Peer id invalid" is silently swallowed.  Any
    other exception is still propagated normally.  This is a class-level
    patch so it affects all Client instances (bot + assistant) — that's fine
    because the bot never receives updates from channels it isn't in.

    This patch is applied once, before any client is started.
    """
    try:
        import pyrogram.client as _pyro_client

        _original_handle_updates = _pyro_client.Client.handle_updates

        async def _safe_handle_updates(self, updates):
            try:
                await _original_handle_updates(self, updates)
            except ValueError as exc:
                if "Peer id invalid" in str(exc):
                    # Drop silently — cache miss on startup, not a real error.
                    pass
                else:
                    raise

        _pyro_client.Client.handle_updates = _safe_handle_updates
        LOGGER.debug("Pyrogram handle_updates patched to suppress peer-id errors.")
    except Exception as exc:
        LOGGER.warning("Could not patch Pyrogram handle_updates: %s", exc)


def validate_config():
    required = ["API_ID", "API_HASH", "BOT_TOKEN", "MONGO_DB_URI", "STRING_SESSION"]
    missing = [k for k in required if not getattr(Config, k, None)]
    if missing:
        LOGGER.critical("Missing required env vars: %s", ", ".join(missing))
        raise SystemExit(1)


def load_plugins():
    """
    Load all plugins by walking melody.plugins using the package's __path__
    (absolute path) — avoids Pyrogram's relative-path auto-loader which
    fails to resolve on Heroku dyno restarts.
    """
    import melody.plugins  # ensure parent package is imported first

    loaded = 0
    failed = 0
    failed_names = []
    music_only = bool(getattr(Config, "MUSIC_ONLY_MODE", False))
    allowed_prefix = "melody.plugins.music."

    for _finder, module_name, is_pkg in pkgutil.walk_packages(
        path=melody.plugins.__path__,
        prefix=melody.plugins.__name__ + ".",
        onerror=lambda name: LOGGER.warning("Plugin walk error: %s", name),
    ):
        if is_pkg:
            continue  # skip __init__ packages, load leaf modules only
        if music_only and not module_name.startswith(allowed_prefix):
            continue
        try:
            importlib.import_module(module_name)
            LOGGER.debug("Loaded plugin: %s", module_name)
            loaded += 1
        except Exception as exc:
            LOGGER.error("Failed to load plugin %s: %s", module_name, exc)
            failed += 1
            failed_names.append(module_name.split(".")[-1])

    LOGGER.info(
        "Plugins loaded: %d OK, %d failed%s",
        loaded,
        failed,
        " (music-only profile)" if music_only else "",
    )
    return loaded, failed, failed_names


async def warm_peer_cache(client, label: str):
    """
    BUG FIX: Heroku dynos have an ephemeral filesystem, so Pyrogram's local
    SQLite peer cache is wiped on every restart/redeploy. Until a chat's
    peer is resolved again, ANY incoming update from that chat makes
    Pyrogram's internal handle_updates() raise
    'ValueError: Peer id invalid: ...' before your command handlers ever
    run — so commands look like they do nothing. Iterating dialogs once
    on startup re-resolves every chat the account is already in and
    prevents this.

    NOTE: only call this for the userbot/assistant client. `get_dialogs()`
    calls messages.getDialogs, which Telegram rejects for bot accounts
    with "400 BOT_METHOD_INVALID" — bots can never use this method, so
    calling it for the bot client is guaranteed to fail every startup.
    """
    try:
        count = 0
        async for _ in client.get_dialogs():
            count += 1
        LOGGER.info("%s: warmed peer cache for %d chats", label, count)
    except Exception as exc:
        LOGGER.warning("%s: could not warm peer cache: %s", label, exc)


async def warm_bot_peer_cache(bot):
    """
    BUG FIX: the bot client can't call get_dialogs() (BOT_METHOD_INVALID),
    so — unlike the assistant — it starts every restart with a completely
    empty local peer cache (Heroku wipes the session file on every
    restart/redeploy). When an update then arrives from a group chat the
    bot hasn't "seen" yet in this process, Pyrogram's own update parser
    raises an UNHANDLED `ValueError: Peer id invalid: ...` deep inside its
    dispatcher/update-parsing internals — this happens before our
    @error_handler-wrapped handlers ever run, so it's invisible in our
    logs and silently drops the update. Symptom: the bot looks fully
    "started" but never responds to any command in existing groups.

    Fix: resolve_peer() IS allowed for bots as long as the chat_id was
    seen before, so we pre-warm using the chat IDs we already persist in
    MongoDB every time the bot is added to a group (see add_chat()).
    """
    chats = None
    for attempt in range(1, 4):
        try:
            chats = await get_all_chats()
            break
        except Exception as exc:
            LOGGER.warning(
                "bot: could not load chats from DB to warm cache (attempt %d/3): %s",
                attempt, exc,
            )
            if attempt < 3:
                await asyncio.sleep(5 * attempt)

    if chats is None:
        LOGGER.warning(
            "bot: giving up warming peer cache — DB unreachable after 3 attempts. "
            "Commands in groups the bot hasn't seen yet this run may not respond "
            "until MongoDB connectivity is restored."
        )
        return

    warmed = 0
    for chat in chats:
        try:
            await bot.resolve_peer(chat["chat_id"])
            warmed += 1
        except Exception:
            pass  # bot likely left/kicked from this chat — skip it

    LOGGER.info("bot: warmed peer cache for %d/%d known chats", warmed, len(chats))


async def warm_recovery_peers(assistant):
    """Resolve peers ONLY for the chats we are about to rejoin on restart.

    Full `get_dialogs()` warm-up takes 8-10 s. Playback recovery must not wait
    for it, but joining a group VC still needs that chat's peer in the local
    cache (fresh dyno = empty SQLite cache). Resolving just the handful of
    chats stored in the playback snapshots costs a few fast RPCs.
    """
    try:
        from utils.playback_state import load_snapshots

        snapshots = await load_snapshots(active_only=True)
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("Could not load snapshots to warm recovery peers: %s", exc)
        return

    for snapshot in snapshots:
        chat_id = snapshot.get("chat_id")
        if not chat_id:
            continue
        try:
            await assistant.resolve_peer(int(chat_id))
        except Exception:  # noqa: BLE001 - recovery handles per-chat failures
            pass


async def register_slash_commands(bot):

    """Register all slash commands with BotFather via set_my_commands()."""
    from pyrogram.types import (
        BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats,
        BotCommandScopeChat, BotCommandScopeDefault,
    )

    # Commands shown in all groups
    group_commands = [
        BotCommand("play",      "▶️ Play a song from YouTube"),
        BotCommand("vplay",     "📹 Play a video stream"),
        BotCommand("pause",     "⏸ Pause playback"),
        BotCommand("resume",    "▶️ Resume playback"),
        BotCommand("skip",      "⏭ Skip current song"),
        BotCommand("stop",      "⏹ Stop music & clear queue"),
        BotCommand("end",       "⏹ Stop music & clear queue"),
        BotCommand("queue",     "📋 Show current queue"),
        BotCommand("np",        "🎵 Now playing info"),
        BotCommand("volume",    "🔊 Set volume (1-200)"),
        BotCommand("mute",      "🔇 Mute playback"),
        BotCommand("unmute",    "🔊 Unmute playback"),
        BotCommand("loop",      "🔂 Loop current song"),
        BotCommand("loopall",   "🔁 Loop entire queue"),
        BotCommand("noloop",    "➡️ Disable loop"),
        BotCommand("shuffle",   "🔀 Shuffle queue"),
        BotCommand("clearqueue","🗑 Clear the queue"),
        BotCommand("remove",    "❌ Remove song from queue"),
        BotCommand("playforce", "⚡ Force-play now (skips queue)"),
        BotCommand("vplayforce","⚡ Force-play video now"),
        BotCommand("playlist",  "📃 Queue an entire playlist"),
        BotCommand("seek",      "⏩ Seek to position (seconds)"),
        BotCommand("seekback",  "⏪ Seek backward (seconds)"),
        BotCommand("speed",     "⚡ Set playback speed (0.5-2.0)"),
        BotCommand("search",    "🔍 Search YouTube"),
        BotCommand("dl",        "⬇️ Download song/video as file"),
        BotCommand("autoplay",  "🤖 Toggle autoplay"),
        BotCommand("ping",      "🏓 Check bot latency"),
        BotCommand("stats",     "📊 Bot statistics"),
        BotCommand("help",      "📖 Help menu"),
        # Music and channel utilities not in the short core list above
        BotCommand("playmode",  "🎛 Access and search mode"),
        BotCommand("queuepos",  "🔢 Show queue position"),
        BotCommand("addplaylist", "➕ Save a playlist"),
        BotCommand("myplaylist", "📃 Show saved playlists"),
        BotCommand("delplaylist", "🗑 Delete a saved playlist"),
        BotCommand("playplaylist", "▶️ Play a saved playlist"),
        BotCommand("live",      "📻 Play a live stream"),
        BotCommand("radio",     "📻 Play radio stream"),
        BotCommand("replay",    "🔄 Replay current stream"),
        BotCommand("channelplay", "📺 Link channel voice chat"),
        BotCommand("joinvc",    "🎧 Join voice chat"),
        BotCommand("leavevc",   "🚪 Leave voice chat"),
        BotCommand("autoend",   "⏹ Auto-end empty voice chat"),
        BotCommand("vcactivity", "🔔 Voice-chat activity alerts"),
        # Frequently used group administration
    ]

    # Commands shown when the bot is added as admin in a channel (channel
    # play). Telegram has no BotCommandScopeAllChannels, so we register
    # these with the default scope, which channels also fall back to.
    channel_commands = [
        BotCommand("cplay",     "▶️ Play in this channel's voice chat"),
        BotCommand("cvplay",    "🎬 Play video in this channel's voice chat"),
        BotCommand("cpause",    "⏸ Pause (channel)"),
        BotCommand("cresume",   "▶️ Resume (channel)"),
        BotCommand("cskip",     "⏭ Skip (channel)"),
        BotCommand("cstop",     "⏹ Stop (channel)"),
        BotCommand("cend",      "⏹ Stop (channel)"),
        BotCommand("cqueue",    "📋 Channel queue"),
        BotCommand("cnp",       "🎵 Channel now playing"),
        BotCommand("cseek",     "⏩ Seek channel stream"),
        BotCommand("cseekback", "⏪ Rewind channel stream"),
        BotCommand("cspeed",    "⚡ Channel playback speed"),
        BotCommand("cvolume",   "🔊 Channel volume"),
        BotCommand("cmute",     "🔇 Mute channel stream"),
        BotCommand("cunmute",   "🔊 Unmute channel stream"),
        BotCommand("cshuffle",  "🔀 Shuffle channel queue"),
        BotCommand("cloop",     "🔂 Loop channel song"),
        BotCommand("cloopall",  "🔁 Loop channel queue"),
        BotCommand("cnoloop",   "➡️ Disable channel loop"),
        BotCommand("help",      "📖 Help menu"),
    ]

    # Public commands available in private chats.
    private_commands = [
        BotCommand("start",     "🎶 Start Apex Vibes"),
        BotCommand("help",      "📖 Help & command list"),
        BotCommand("ping",      "🏓 Check bot latency"),
        BotCommand("stats",     "📊 Bot statistics"),
        BotCommand("about",     "ℹ️ About Apex Vibes"),
        BotCommand("alive",     "💚 Check uptime"),
    ]

    owner_commands = [
        BotCommand("panel",     "🛠 Owner control panel"),
        BotCommand("logs",      "📜 View bot logs"),
        BotCommand("chatlist",  "👥 List stored chats"),
        BotCommand("maintenance", "🔧 Maintenance tools"),
        BotCommand("restart",   "🔄 Restart bot"),
        BotCommand("reboot",    "♻️ Reboot runtime"),
        BotCommand("update",    "⬆️ Update bot code"),
        BotCommand("eval",      "🧪 Run owner evaluation"),
        BotCommand("shell",     "💻 Run owner shell"),
        BotCommand("sysinfo",   "📊 System information"),
        BotCommand("speedtest", "🌐 Network speed test"),
        BotCommand("logger",    "📝 Logging controls"),
        BotCommand("emojiid",   "🙂 Inspect custom emoji ID"),
        BotCommand("setpic",    "🖼 Set start picture"),
        BotCommand("delpic",    "🗑 Delete start picture"),
        BotCommand("setwelcomepic", "🖼 Set welcome picture"),
        BotCommand("delwelcomepic", "🗑 Delete welcome picture"),
        BotCommand("setsource", "🔗 Set source link"),
        BotCommand("ocmds",     "📚 Owner command vault"),
    ]

    if Config.MUSIC_ONLY_MODE:
        music_commands = {
            "play", "vplay", "pause", "resume", "skip", "stop", "end",
            "queue", "np", "volume", "mute", "unmute", "loop", "loopall",
            "noloop", "shuffle", "clearqueue", "remove", "playlist", "seek",
            "seekback", "speed", "search", "dl", "autoplay",
        }
        group_commands = [
            command for command in group_commands if command.command in music_commands
        ]
        channel_commands = [
            command for command in channel_commands
            if command.command in {"cplay", "cvplay", "cpause", "cresume", "cskip", "cstop", "cend"}
        ]
        private_commands = []

    try:
        await bot.set_bot_commands(group_commands[:100], scope=BotCommandScopeAllGroupChats())
        await bot.set_bot_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
        if Config.OWNER_ID:
            await bot.set_bot_commands(
                private_commands + owner_commands,
                scope=BotCommandScopeChat(int(Config.OWNER_ID)),
            )
        # Groups/private already have more specific scopes above, so the
        # default scope only applies where nothing else matches — i.e.
        # channels, which Pyrogram/Telegram has no dedicated scope for.
        await bot.set_bot_commands(channel_commands, scope=BotCommandScopeDefault())
        LOGGER.info(
            "Slash commands registered: %d group, %d private, %d owner, %d channel",
            len(group_commands), len(private_commands), len(owner_commands),
            len(channel_commands),
        )
    except Exception as exc:
        LOGGER.warning("Could not register slash commands: %s", exc)


async def sync_log_group_peer(bot, assistant):
    """
    ROOT-CAUSE FIX for "Peer id invalid: LOG_GROUP_ID" on bot client.

    WHY IT HAPPENS
    ══════════════
    Heroku wipes every dyno's ephemeral filesystem on restart, which
    destroys Pyrogram's local SQLite session files.  Pyrogram resolves a
    raw chat ID (e.g. -1004334848663) to an InputPeerChannel only when it
    has previously cached that channel's access_hash — without it the call
    raises "Peer id invalid".  The bot cannot self-populate this cache
    because:
      • get_dialogs() → Telegram rejects it with BOT_METHOD_INVALID
      • get_chat(int_id) internally calls resolve_peer which fails the
        same way
    So the bot ALWAYS starts with an empty peer DB and ALWAYS fails to
    send to any channel it hasn't received a live update from yet.

    WHY THE LOG CHANNEL SPECIFICALLY
    ═════════════════════════════════
    The log channel is a broadcast channel (only admins can post).  The
    bot is an admin; the assistant is a subscriber.  Swapping the sender
    to the assistant fails with CHAT_ADMIN_REQUIRED.  So we must keep the
    bot as the sender — we just need to give it the access_hash.

    THE FIX
    ═══════
    The assistant already called get_dialogs() on startup and has the log
    channel's access_hash in its SQLite cache.  We call
    assistant.resolve_peer(LOG_GROUP_ID) — which succeeds — and then write
    the resulting (id, access_hash, type) tuple directly into the bot
    client's SQLite peer table via bot.storage.update_peers().  From that
    point forward, bot.resolve_peer(LOG_GROUP_ID) succeeds for this
    process lifetime, so bot.send_message() works without any incoming
    update from the channel.
    """
    if not Config.LOG_GROUP_ID:
        return
    try:
        from pyrogram.raw.types import InputPeerChannel
        peer = await assistant.resolve_peer(Config.LOG_GROUP_ID)
        if isinstance(peer, InputPeerChannel):
            # update_peers signature: list of (id, access_hash, type, username, phone)
            # `id` must be the full signed integer Pyrogram uses as the chat key,
            # i.e. -(1_000_000_000_000 + channel_id) — same value as LOG_GROUP_ID.
            # ROOT-CAUSE FIX (log: "Incorrect number of bindings supplied.
            # The current statement uses 4, and there are 5 supplied"):
            # different Pyrogram forks store a different number of peer
            # columns (id, access_hash, type[, username][, phone_number]).
            # Passing a fixed 5-tuple therefore breaks on 4-column forks, so
            # we try the widest tuple first and shrink until SQLite accepts it.
            base = (Config.LOG_GROUP_ID, peer.access_hash, "channel")
            last_exc = None
            for extra in ((None, None), (None,), ()):
                try:
                    await bot.storage.update_peers([base + extra])
                    last_exc = None
                    break
                except Exception as exc:  # arity / schema mismatch
                    last_exc = exc
            if last_exc is not None:
                raise last_exc
            LOGGER.info(
                "bot: synced LOG_GROUP_ID peer (access_hash injected) from assistant cache"
            )

        else:
            # Ordinary group chat — Pyrogram resolves plain group IDs by chat_id
            # without needing an access_hash, so no injection required.
            LOGGER.debug("bot: log group is a plain group — no peer injection needed")
    except Exception as exc:
        LOGGER.warning("bot: could not sync log group peer from assistant: %s", exc)


async def send_startup_log(bot, assistant, loaded: int, failed: int, failed_names: list):
    """Send a detailed startup message to LOG_GROUP_ID with owner DM fallback.

    ROOT-CAUSE FIX for persistent CHANNEL_INVALID on startup log:

    WHY IT KEPT FAILING
    ═══════════════════
    sync_log_group_peer() injects the access_hash into bot's SQLite so
    bot.resolve_peer() succeeds — but Telegram still rejects
    messages.SendMessage with CHANNEL_INVALID when the calling account
    (bot or assistant) is NOT a member / admin of that channel, or the
    channel ID stored in LOG_GROUP_ID no longer exists / was migrated.
    Peer resolution and channel membership are two separate things:
    having the access_hash doesn't mean you have posting rights.

    THE FIX
    ═══════
    Try in order:
      1. assistant → LOG_GROUP_ID   (assistant is usually a subscriber)
      2. bot       → LOG_GROUP_ID   (bot if it's channel admin)
      3. bot       → OWNER_ID DM    (always works as long as owner has
                                     started the bot at least once)

    Each attempt is isolated so one failure never silences the others.
    The destination that worked is logged so misconfiguration is visible.
    """
    if not Config.LOG_GROUP_ID and not Config.OWNER_ID:
        return
    try:
        me = await bot.get_me()
        py_ver = platform.python_version()
        import pyrogram
        pyrogram_ver = pyrogram.__version__

        status_line = f"✅ {loaded} plugins OK" + (
            f", ❌ {failed} failed: {'  '.join(failed_names)}" if failed else ""
        )

        text = (
            "╔══════════════════════════════╗\n"
            "║   🎶  <b>MELODY IS LIVE!</b>     ║\n"
            "╚══════════════════════════════╝\n\n"
            f"🤖 <b>Bot:</b> @{me.username} (<code>{me.id}</code>)\n"
            f"🐍 <b>Python:</b> <code>{py_ver}</code>\n"
            f"📦 <b>Pyrogram:</b> <code>{pyrogram_ver}</code>\n\n"
            f"🔌 <b>Plugins:</b> {status_line}\n"
            f"🌐 <b>Platform:</b> <code>{platform.system()} {platform.release()}</code>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ All systems operational. Ready to receive commands!"
        )

        # Attempt 1: assistant → log channel
        if Config.LOG_GROUP_ID:
            try:
                await assistant.send_message(Config.LOG_GROUP_ID, text)
                LOGGER.info("Startup log sent via assistant → LOG_GROUP_ID")
                return
            except Exception as e1:
                LOGGER.debug("assistant→LOG_GROUP_ID failed: %s", e1)

        # Attempt 2: bot → log channel
        if Config.LOG_GROUP_ID:
            try:
                await bot.send_message(Config.LOG_GROUP_ID, text)
                LOGGER.info("Startup log sent via bot → LOG_GROUP_ID")
                return
            except Exception as e2:
                LOGGER.debug("bot→LOG_GROUP_ID failed: %s", e2)

        # Attempt 3: bot → owner DM (permanent fallback)
        # Works as long as the owner has started the bot at least once.
        # If LOG_GROUP_ID is broken/wrong, owner will see the log here.
        if Config.OWNER_ID:
            try:
                owner_note = ""
                if Config.LOG_GROUP_ID:
                    owner_note = (
                        "\n\n⚠️ <b>Note:</b> Startup log sent to your DM because "
                        "neither bot nor assistant could send to LOG_GROUP_ID "
                        f"<code>{Config.LOG_GROUP_ID}</code>. "
                        "Check that the bot/assistant is a member of that channel."
                    )
                await bot.send_message(Config.OWNER_ID, text + owner_note)
                LOGGER.info("Startup log sent via bot → OWNER_ID DM (LOG_GROUP_ID unreachable)")
                return
            except Exception as e3:
                LOGGER.warning(
                    "Startup log could not be delivered anywhere. "
                    "LOG_GROUP_ID error: see debug logs. OWNER_ID DM error: %s", e3
                )
    except Exception as exc:
        LOGGER.warning("Could not send startup log: %s", exc)


async def main():
    validate_config()

    # ROOT-CAUSE FIX (bot came online but nothing ever played in VC):
    # the apt-installed ffmpeg aborted on a missing libpulsecommon-*.so, so
    # every stream ended instantly ("premature ends") while the bot kept
    # answering/reacting to messages. Verify + repair ffmpeg BEFORE any call
    # is created. See utils/ffmpeg_runtime.py for the full explanation.
    try:
        from utils.ffmpeg_runtime import ensure_ffmpeg_runtime
        await asyncio.to_thread(ensure_ffmpeg_runtime)
    except Exception as exc:
        LOGGER.warning("ffmpeg runtime check failed: %s", exc)

    # FIX: restore GitHub-persisted images (start pic, group-welcome pic)
    # BEFORE plugins load. Ephemeral filesystems (Heroku) wipe assets/ on
    # every restart; /setpic and /setwelcomepic push to GitHub on save, but
    # that's only "permanent" if the fresh dyno also pulls it back down on
    # boot. Best-effort — silently skipped if GITHUB_TOKEN/GITHUB_REPO
    # aren't configured, and never blocks startup.
    try:
        from utils.github_assets import restore_persistent_assets
        import os as _os
        _assets_dir = _os.path.join(_os.path.dirname(__file__), "..", "assets")
        await restore_persistent_assets([
            (_os.path.join(_assets_dir, "bg_start.png"), "assets/bg_start.png"),
            (_os.path.join(_assets_dir, "bg_welcome.png"), "assets/bg_welcome.png"),
        ])
    except Exception as exc:
        LOGGER.warning("Could not restore persistent assets from GitHub: %s", exc)

    # FIX: Patch Pyrogram BEFORE any client is created so the class-level
    # override is in place by the time bot.start() / assistant.start() run.
    # See _patch_pyrogram_peer_errors() docstring for full explanation.
    _patch_pyrogram_peer_errors()

    # FLOOD_WAIT FIX: cache get_chat()/get_users() results for a few minutes.
    # Without this, channels.GetFullChannel / users.GetFullUser were re-issued
    # for the same chats on every play + VC event, and Telegram answered with
    # multi-second FLOOD_WAITs (visible as "Waiting for N seconds" warnings).
    from utils.chat_cache import install_chat_cache
    install_chat_cache()

    # FIX: Create Pyrogram clients AFTER validate_config() passes.
    # melody/__init__.py intentionally does NOT create clients at import time
    # because `python -m melody` imports the melody package (runs __init__.py)
    # BEFORE any code here executes — i.e. before validate_config() runs.
    # Creating clients with empty/invalid env vars (especially session_string="")
    # raises ValueError silently and kills the process with zero log output.
    from melody import create_clients
    create_clients()
    LOGGER.info("Pyrogram clients created.")

    # Load all plugins BEFORE starting clients so decorators register handlers
    loaded, failed, failed_names = load_plugins()
    LOGGER.info("All plugins loaded.")

    from melody import bot, assistant
    from melody.core.call import recover_playback, start_call_py

    # Auto-refresh cookies from COOKIES_URL if set
    try:
        await refresh_cookies_from_url()
    except Exception as _e:
        LOGGER.warning(f"Cookie auto-refresh skipped: {_e}")

    LOGGER.info("Starting Apex Vibes...")

    await bot.start()
    LOGGER.info("Bot client started.")

    # ─── HEROKU LOG FIX: AUTH_KEY_DUPLICATED crash-loop ──────────────────────
    # Logs showed the dyno dying every ~20 s with:
    #   pyrogram.errors...AuthKeyDuplicated: [406 AUTH_KEY_DUPLICATED]
    # That happens when the SAME assistant STRING_SESSION is logged in from two
    # places at once (two dynos / local run + Heroku, or a re-deploy overlap).
    # Telegram then invalidates the session, assistant.start() raises, the
    # whole process exits with status 1 and Heroku restarts it forever — so the
    # bot never answered anything at all.
    # Now: the failure is caught, explained once in the logs, and the bot keeps
    # running in "bot-only" mode instead of crash-looping. Music needs the
    # assistant, so /play replies with a clear message (see ASSISTANT_READY).
    import melody as _melody_pkg

    assistant_ok = True
    try:
        await assistant.start()
        LOGGER.info("Assistant client started.")
    except Exception as exc:  # noqa: BLE001
        assistant_ok = False
        name = type(exc).__name__
        if name in {
            "AuthKeyDuplicated",
            "AuthKeyUnregistered",
            "AuthKeyInvalid",
            "SessionRevoked",
            "SessionExpired",
            "UserDeactivated",
            "UserDeactivatedBan",
        }:
            LOGGER.error(
                "❌ ASSISTANT SESSION INVALID (%s).\n"
                "   Iska matlab: yehi STRING_SESSION ek se zyada jagah chal raha hai, "
                "ya Telegram ne use revoke kar diya hai.\n"
                "   Fix: (1) purane dyno / local run band karo, "
                "(2) naya STRING_SESSION generate karo (python3 genstring.py), "
                "(3) Heroku config var STRING_SESSION update karke restart karo.\n"
                "   Bot text commands ke liye chalu rahega, lekin voice chat "
                "(/play) tab tak kaam nahi karega.",
                name,
            )
        else:
            LOGGER.error("❌ Assistant client start failed: %s: %s", name, exc)

    _melody_pkg.ASSISTANT_READY = assistant_ok

    # ═══════════════════════════════════════════════════════════════════════
    # 🔊 PRIORITY #1 ON RESTART: resume voice-chat playback FIRST
    # ═══════════════════════════════════════════════════════════════════════
    # Users ko wait na karna pade: jo /play or /vplay restart ke waqt chal
    # raha tha wo sabse pehle wapas start hota hai. Emoji resolution, peer
    # cache warm-up (get_dialogs takes 8-10 s), slash commands, startup log
    # etc. — sab iske BAAD chalta hai (mostly background tasks).
    if assistant_ok:
        try:
            await start_call_py()
            LOGGER.info("PyTgCalls started.")
        except Exception as exc:  # noqa: BLE001
            assistant_ok = False
            _melody_pkg.ASSISTANT_READY = False
            LOGGER.error("PyTgCalls failed to start: %s", exc)
    else:
        LOGGER.warning(
            "⚠️ PyTgCalls skipped — assistant session invalid. "
            "Bot online hai, par VC playback disabled."
        )

    if assistant_ok:
        try:
            from utils.playback_state import ensure_indexes as ensure_playback_indexes

            await ensure_playback_indexes()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Playback-state index setup skipped: %s", exc)

        async def _recover_in_background():
            try:
                # Recovery is deliberately detached from command startup. It
                # can touch many chats and must never compete with a fresh
                # manual /play or make the bot look frozen after deploy.
                await warm_recovery_peers(assistant)
                recovered = await recover_playback()
                LOGGER.info("Recovered playback in %d chat(s).", recovered)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Playback-state recovery skipped: %s", exc)

        if Config.PLAYBACK_RECOVERY:
            spawn(_recover_in_background())
        else:
            LOGGER.info("Playback recovery disabled by default; fresh /play owns playback.")

    # ═══════════════════════════════════════════════════════════════════════
    # Everything below is secondary startup work.
    # ═══════════════════════════════════════════════════════════════════════

    if not Config.MUSIC_ONLY_MODE:
        # Premium-emoji resolvers belong to the broad UI surface. The music
        # profile uses the static icon map and avoids extra startup RPC/tasks.
        from utils.emoji_map import resolve_emoji_map

        async def _resolve_emoji_map_bg():
            try:
                await resolve_emoji_map(bot)
            except Exception as exc:  # noqa: BLE001 - never block startup
                LOGGER.warning("Premium emoji map resolution failed: %s", exc)

        spawn(_resolve_emoji_map_bg())

        try:
            from utils import emoji_search

            emoji_search.start(assistant if assistant_ok else bot)
        except Exception as exc:  # noqa: BLE001 - never block startup
            LOGGER.warning("Universal premium-emoji resolver failed to start: %s", exc)

    # Re-resolve peers for chats we're already in (see warm_peer_cache docstring).
    # Only the assistant/userbot account can call get_dialogs() — bots always
    # get rejected with "BOT_METHOD_INVALID", so don't waste a call on `bot`.
    assistant_warm_task = (
        spawn(warm_peer_cache(assistant, "assistant"), name="assistant-peer-warm")
        if assistant_ok
        else None
    )
    # The bot client needs its own warm-up (see warm_bot_peer_cache docstring) —
    # run it in the background so a large Mongo chat list can never delay the
    # first user command after deploy.
    spawn(warm_bot_peer_cache(bot), name="bot-peer-warm")

    try:
        from utils.song_cache import ensure_indexes as ensure_song_cache_indexes

        await ensure_song_cache_indexes()
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Song-cache index build skipped: %s", exc)

    # Expensive yt-dlp/Deno warm-ups are opt-in. Starting them on every deploy
    # created a large CPU/RSS spike before the first user command, especially on
    # a 512 MB dyno. Playback starts the same helpers lazily when a fallback
    # actually needs them; larger deployments can opt into boot pre-warming.
    # Warm the lightweight PO-token HTTP provider before the first /play.
    # Otherwise the first direct resolver starts Deno/bgutil on the command
    # path and adds the 4-8s delay visible in production logs. Low-memory
    # workers remain protected; other workers can explicitly opt out with 0.
    if not Config._LOW_MEMORY_PROFILE or Config.STARTUP_WARMUPS:
        from melody.core.ytdl import warm_up_bgutil_server
        try:
            # Do not merely spawn this task: a first /play can arrive before
            # bgutil is ready and select the slower script provider. Awaiting
            # the bounded warmup makes the fast HTTP provider deterministic.
            await warm_up_bgutil_server(timeout=15.0)
        except Exception as exc:  # noqa: BLE001 - playback keeps a fallback
            LOGGER.warning("bgutil startup warmup skipped: %s", exc)
    if Config.STARTUP_WARMUPS:
        from melody.core.ytdl import warm_popular_metadata
        spawn(warm_popular_metadata())

    # Prime the direct-CDN resolver itself (yt-dlp player JS + nsig cache +
    # PO-token round-trip). Without this the FIRST /play after a restart pays
    # a ~5s cold-start that later plays never pay — exactly the 11s vs 5.5s
    # split visible in production logs. Fire-and-forget; failures are silent.
    if not Config._LOW_MEMORY_PROFILE or Config.STARTUP_WARMUPS:
        async def _warm_direct_resolver() -> None:
            try:
                from melody.core.ytdl import resolve_stream_urls
                await resolve_stream_urls("dQw4w9WgXcQ", force=True)
                LOGGER.info("✅ direct-CDN resolver pre-warmed (first /play stays fast)")
            except Exception as exc:  # noqa: BLE001
                LOGGER.info("direct-CDN resolver warmup skipped: %s", exc)
        spawn(_warm_direct_resolver())

    # R14 FIX: keep resident memory from ratcheting up until Heroku reports
    # "Error R14 (Memory quota exceeded)". See utils/memguard.py.
    from utils.memguard import memory_guard
    spawn(memory_guard())

    # 💾 Keep /tmp under control: sweep last run's orphans at boot and evict
    # cached songs by LRU / free-space pressure while the bot runs. A full
    # dyno disk is one of the loudest causes of "gana nahi bajta".
    from utils.diskguard import disk_guard
    spawn(disk_guard())

    # Hang up on voice chats everybody has left (see auto_leave_watchdog).
    from melody.core.call import auto_leave_watchdog
    spawn(auto_leave_watchdog())

    # 🛰 Watch live voice chats from OUTSIDE the call (roster polling only).
    # The assistant never joins a VC unless it is actually playing music.
    try:
        from melody.core import vc_listener
        vc_listener.start()
    except Exception:
        LOGGER.debug("vc_listener could not start", exc_info=True)

    # Register slash commands with BotFather
    await register_slash_commands(bot)

    async def _finish_secondary_startup():
        # Peer warming and startup-log delivery are dependent but secondary;
        # none of them should keep the command dispatcher waiting.
        if assistant_warm_task is not None:
            try:
                await assistant_warm_task
            except Exception:  # noqa: BLE001
                LOGGER.debug("assistant peer warm-up failed", exc_info=True)
        await sync_log_group_peer(bot, assistant)
        await send_startup_log(bot, assistant, loaded, failed, failed_names)

    spawn(_finish_secondary_startup(), name="startup-log-sync")
    LOGGER.info("🎶 Apex Vibes is live!")
    try:
        await asyncio.Event().wait()  # Keep running
    finally:
        # Cancel every in-flight background task so a restart never leaves
        # half-finished work (downloads, cleanups) writing to a dying loop.
        from utils.tasks import cancel_all
        await cancel_all()


if __name__ == "__main__":
    uvloop.install()
    asyncio.run(main())
