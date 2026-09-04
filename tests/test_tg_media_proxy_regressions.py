import asyncio


def test_concurrent_chunk_waiters_receive_shared_bytes(monkeypatch):
    from utils import tg_media_proxy as proxy

    async def run():
        entry = object()
        calls = 0

        async def fake_read_chunk_inner(_entry, _index):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return b"chunk-data"

        monkeypatch.setattr(proxy, "_read_chunk_inner", fake_read_chunk_inner)
        proxy_entry = type("Entry", (), {"cache": {}, "chunk_futures": {}})()
        result = await asyncio.gather(
            proxy._read_chunk(proxy_entry, 7),
            proxy._read_chunk(proxy_entry, 7),
        )
        assert result == [b"chunk-data", b"chunk-data"]
        assert calls == 1
        assert proxy_entry.chunk_futures == {}

    asyncio.run(run())
