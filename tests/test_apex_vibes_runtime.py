from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def test_music_entrypoint_and_procfile():
    assert 'python -m melody' in read('Procfile')
    assert 'python3 -m melody' in read('start')
    assert 'melody.plugins.music.' in read('melody/__main__.py')


def test_only_music_plugins_are_present():
    plugins = {p.relative_to(ROOT / 'melody/plugins').as_posix() for p in (ROOT / 'melody/plugins').rglob('*.py')}
    assert all(p.startswith(('__init__.py', 'music/')) for p in plugins)
    assert not any('lyrics' in p for p in plugins)


def test_playback_pipeline_has_fast_fallback_and_cdn_headers():
    ytdl = read('melody/core/ytdl.py')
    call = read('melody/core/call.py')
    play = read('melody/plugins/music/play.py')
    assert 'android_vr' in ytdl
    assert 'missing_pot' in ytdl
    assert 'resolve_stream_urls' in ytdl
    assert 'headers=headers' in call
    assert 'download_audio' in play


def test_vc_live_chat_modules_remain_available():
    assert (ROOT / 'melody/core/vc_listener.py').exists()
    assert (ROOT / 'melody/core/vc_notify.py').exists()
    assert (ROOT / 'melody/core/vc_chat_log.py').exists()
    source = read('melody/core/vc_listener.py') + read('melody/core/vc_notify.py')
    assert 'voice' in source.lower()


def test_removed_features_are_not_registered():
    main = read('melody/__main__.py')
    controls = read('melody/plugins/music/controls.py')
    for command in ('lyrics', 'revoke', 'extra', 'groupmanager', 'group_manager'):
        assert f'botcommand("{command}"' not in main.lower()
        assert f'filters.command("{command}"' not in controls.lower()
    assert 'lyrics_callback' not in controls
    assert 'lyricsgenius' not in controls
