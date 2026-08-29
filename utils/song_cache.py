"""Ephemeral song metadata cache; persistent media storage is intentionally disabled."""
from __future__ import annotations

import json
import sqlite3
import threading
import time


async def remember_song(video_id: str, video: bool, file_id: str) -> None:
    """Compatibility no-op: Telegram/Mongo song-file indexing is disabled."""
    return None


async def restore_song(client, video_id: str, video: bool) -> str | None:
    """Compatibility no-op: media is never restored from MongoDB."""
    return None


async def remember_completed_file(video_id: str, video: bool, filepath: str) -> bool:
    """Compatibility no-op: completed media is never uploaded to MongoDB."""
    return False


async def ensure_indexes() -> None:
    """Compatibility no-op: no MongoDB cache collection is used."""
    return None


# Query/video metadata is kept only in the dyno's ephemeral /tmp filesystem.
# It contains small JSON metadata, never audio/video bytes, and disappears when
# the dyno restarts or is redeployed.
_META_DB_PATH = "/tmp/melody_meta_cache.sqlite3"
_meta_lock = threading.Lock()
_meta_conn: sqlite3.Connection | None = None


def _get_meta_conn() -> sqlite3.Connection:
    global _meta_conn
    if _meta_conn is None:
        _meta_conn = sqlite3.connect(_META_DB_PATH, check_same_thread=False)
        _meta_conn.execute(
            "CREATE TABLE IF NOT EXISTS meta_cache ("
            "key TEXT PRIMARY KEY, data TEXT NOT NULL, ts REAL NOT NULL)"
        )
        _meta_conn.commit()
    return _meta_conn


def get_persistent_meta(key: str, ttl: float = 259200.0) -> dict | None:
    """Read small query metadata from ephemeral local SQLite."""
    if not key:
        return None
    try:
        with _meta_lock:
            row = _get_meta_conn().execute(
                "SELECT data, ts FROM meta_cache WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return None
        data_raw, ts = row
        if time.time() - ts > ttl:
            return None
        return json.loads(data_raw)
    except Exception:
        return None


def put_persistent_meta(key: str, data: dict) -> None:
    """Write small query metadata to ephemeral local SQLite only."""
    if not key or not data:
        return
    try:
        with _meta_lock:
            conn = _get_meta_conn()
            conn.execute(
                "INSERT INTO meta_cache (key, data, ts) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET data = excluded.data, ts = excluded.ts",
                (key, json.dumps(data), time.time()),
            )
            conn.commit()
    except Exception:
        pass
