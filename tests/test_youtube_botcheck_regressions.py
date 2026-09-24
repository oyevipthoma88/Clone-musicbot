from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_build_installs_cloud_youtube_token_provider():
    source = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "bin/post_compile /app" in source
    # Heroku's Python buildpack only runs bin/post_compile when the file is
    # executable. A 0644 hook silently skipped the entire PO-token install.
    assert (ROOT / "bin/post_compile").stat().st_mode & 0o111


def test_youtube_proxy_is_applied_to_both_resolvers():
    source = (ROOT / "melody/core/ytdl.py").read_text(encoding="utf-8")
    assert 'os.getenv("YTDLP_PROXY", "").strip()' in source
    assert 'kwargs["proxy"] = proxy' in source
    assert 'opts["proxy"] = proxy' in source


def test_proxy_is_documented_as_youtube_recovery_setting():
    source = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "YTDLP_PROXY=" in source
    assert "bot-checked" in source


if __name__ == "__main__":
    test_docker_build_installs_cloud_youtube_token_provider()
    test_youtube_proxy_is_applied_to_both_resolvers()
    test_proxy_is_documented_as_youtube_recovery_setting()
    print("youtube bot-check regression tests: PASS")
