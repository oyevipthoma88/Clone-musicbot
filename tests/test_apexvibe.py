import ast
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = (ROOT / "apexvibe.py").read_text(encoding="utf-8")


def test_apexvibe_parses_and_has_one_runtime_script():
    ast.parse(SOURCE, filename="apexvibe.py")
    assert (ROOT / "apexvibe.py").is_file()
    assert not (ROOT / "melody").exists()


def test_only_play_and_skip_are_registered():
    tree = ast.parse(SOURCE)
    commands = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "command":
            if not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant):
                commands.append(arg.value)
            elif isinstance(arg, (ast.List, ast.Tuple)):
                commands.extend(item.value for item in arg.elts if isinstance(item, ast.Constant))
    assert "play" in commands and "skip" in commands
    assert {"pause", "resume", "stop", "queue", "volume", "seek", "speed"}.issubset(commands)
    assert 'filters.command("autoplay")' not in SOURCE
    assert "AsyncIOMotorGridFSBucket" not in SOURCE
    assert "calls.stop()" not in SOURCE


def test_playback_has_generation_fences_and_single_download_gate():
    assert "_download_lock = asyncio.Lock()" in SOURCE
    assert "def _is_current" in SOURCE
    assert "if not _is_current(chat_id, generation):" in SOURCE
    assert "state.generation += 1" in SOURCE
    assert "old_task.cancel()" in SOURCE
    assert "MAX_DOWNLOAD_SECONDS" in SOURCE
    assert "nopart" in SOURCE
    assert "os.replace(candidates[0], target)" in SOURCE
    assert "-reconnect_streamed 1" in SOURCE


def test_youtube_cloud_fallback_and_api_rate_limit_guard_are_present():
    assert '"player_client": ["android_vr", "tv", "ios", "web_safari"]' in SOURCE
    assert '"formats": ["missing_pot"]' in SOURCE
    assert '"external_downloader": {"default": "native"}' in SOURCE
    assert '"fixup": "never"' in SOURCE
    assert 'kwargs["headers"]' in SOURCE
    assert '"Referer": "https://www.youtube.com/"' in SOURCE
    assert "_youtube_api_disabled_until" in SOURCE
    assert "response.status_code == 429" in SOURCE
    assert "disabling API search for 5 minutes" in SOURCE


def test_controls_are_detached_from_voice_transition():
    assert 'await message.reply_text("⏭ Skipping…")' in SOURCE
    assert '_spawn(_skip(message.chat.id)' in SOURCE
    assert "await asyncio.wait_for(calls.leave_call(chat_id), timeout=CONTROL_TIMEOUT)" in SOURCE


def test_stream_end_has_a_conservative_early_end_fence():
    assert "ignoring early stale StreamEnded" in SOURCE
    assert "time.monotonic() - state.started_at < 1.5" in SOURCE
    assert "state.current.duration > 3" in SOURCE
    assert "await _start_next(chat_id)" in SOURCE


def test_autoplay_is_bounded_and_manual_play_can_take_over():
    assert "AUTOPLAY_ENABLED = _flag_env(\"AUTOPLAY\", True)" in SOURCE
    assert "AUTOPLAY_TIMEOUT" in SOURCE
    assert "async def _autoplay_next" in SOURCE
    assert "state.transition_task.cancel()" in SOURCE
    assert "await _takeover_autoplay(chat_id)" in SOURCE
    assert "state.generation != token" in SOURCE


def test_melody_style_cards_and_inline_controls_are_bounded():
    assert "async def _make_thumbnail" in SOURCE
    assert "Image.open(io.BytesIO(data))" in SOURCE
    assert "len(data) > 4 * 1024 * 1024" in SOURCE
    assert "def _play_keyboard" in SOURCE
    assert 'callback_data="av:pause"' in SOURCE
    assert 'callback_data="av:skip"' in SOURCE
    assert "@client.on_callback_query" in SOURCE
    assert "await _send_play_card" in SOURCE
    assert "Pillow" in (ROOT / "requirements.txt").read_text(encoding="utf-8")


def test_clone_setup_is_verified_bounded_and_secret_safe():
    assert "def _verify_bot_token" in SOURCE
    assert "getMe" in SOURCE
    assert "def _verify_assistant" in SOURCE
    assert "in_memory=True" in SOURCE
    assert "def _clone_audit_text" in SOURCE
    assert "_mask(config.get('bot_token'" in SOURCE
    assert "string_session_sha256" in SOURCE
    assert "asyncio.create_subprocess_exec" in SOURCE
    assert "MAX_ACTIVE_CLONES" in SOURCE
    assert "CLONE_MODE" in SOURCE
    assert "stdout=asyncio.subprocess.DEVNULL" in SOURCE
    assert "stderr=asyncio.subprocess.DEVNULL" in SOURCE
    assert "from cryptography.fernet import Fernet, InvalidToken" in SOURCE
    assert "def _registry_upsert_sync" in SOURCE
    assert "def _registry_active_sync" in SOURCE
    assert "async def _restore_persisted_clones" in SOURCE
    assert '"deadline": time.monotonic() + 900' in SOURCE


def test_clone_setup_ui_and_parent_only_registration_exist():
    assert "Make Your Own Music Bot" in SOURCE
    assert "Create Free Music Bot" in SOURCE
    assert "Free Music Tutorial" in SOURCE
    assert 'filters.command("start") & filters.private' in SOURCE
    assert 'filters.command("tutorial") & filters.private' in SOURCE
    assert 'if not CLONE_MODE:' in SOURCE
    assert "register_clone_setup_handlers(bot)" in SOURCE
    assert "CLONE_USERS_LOG" in SOURCE
    assert "MONGO_DB_URI" in SOURCE
    assert "seed = f\"{API_HASH}:{BOT_TOKEN}\"" in SOURCE


def test_youtube_credentials_are_environment_only():
    assert 'os.getenv("BOT_TOKEN"' in SOURCE
    assert 'os.getenv("STRING_SESSION"' in SOURCE
    assert 'os.getenv("YT_COOKIES"' in SOURCE
    assert 'os.getenv("COOKIE_URL"' not in SOURCE
    assert 'os.getenv("YOUTUBE_API_KEY"' in SOURCE
    assert "github_pat_" not in SOURCE
    assert "api_key=" not in SOURCE.lower()


def test_heroku_files_are_minimal_and_consistent():
    app = json.loads((ROOT / "app.json").read_text(encoding="utf-8"))
    assert app["formation"]["worker"]["quantity"] == 1
    required = {
        "API_ID", "API_HASH", "BOT_TOKEN", "STRING_SESSION", "MONGO_DB_URI",
        "LOG_GROUP_ID", "OWNER_ID", "OWNER_USERNAME", "YOUTUBE_API_KEY",
        "YT_COOKIES", "CLONE_USERS_LOG",
    }
    assert required.issubset(app["env"])
    assert set(app["env"]) == required
    assert app["env"]["CLONE_USERS_LOG"]["required"] is True
    assert app["env"]["MONGO_DB_URI"]["required"] is True
    assert (ROOT / "Procfile").read_text(encoding="utf-8").startswith("worker:")
    assert (ROOT / "Aptfile").read_text(encoding="utf-8").strip() == "ffmpeg"


def test_readme_explains_private_repo_button_limit_and_fast_path():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Deploy to Heroku" in readme
    assert "oyevipthoma88/Clone-musicbot" in readme
    assert "direct YouTube audio URL" in readme
    assert "growing `.part`" in readme
