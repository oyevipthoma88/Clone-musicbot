from pathlib import Path
import json

ROOT = Path(__file__).parents[1]


def test_removed_feature_plugins_are_absent():
    plugins = ROOT / 'melody' / 'plugins'
    assert not (plugins / 'admin').exists()
    assert not (plugins / 'music' / 'lyrics.py').exists()
    for name in ('economy.py', 'social.py', 'whisper.py'):
        assert not (plugins / 'misc' / name).exists()


def test_start_and_help_contain_only_music_vc_owner_surface():
    text = (ROOT / 'melody/plugins/misc/start.py').read_text()
    help_text = (ROOT / 'melody/plugins/misc/help.py').read_text()
    combined = (text + help_text).lower()
    for forbidden in ('economy', 'social gif', 'whisper', 'group management', 'moderation', 'lyrics', 'chatbot'):
        assert forbidden not in combined
    for required in ('play music', 'voice chat', 'owner panel', '/play', '/queue'):
        assert required in combined


def test_audio_play_uses_parallel_vc_prejoin():
    text = (ROOT / 'melody/plugins/music/play.py').read_text()
    assert 'spawn(pre_join(chat.id)' in text
    assert 'if not video:' in text


def test_app_json_is_valid():
    json.loads((ROOT / 'app.json').read_text())
