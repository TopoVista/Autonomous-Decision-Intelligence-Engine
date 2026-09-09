"""Session memory with SQLite persistence fallback.

Priority order:
  1. Redis (when REDIS_URL is set and reachable)
  2. aiosqlite-backed session_turns table (always available; survives restarts)

The SQLite fallback replaces the old in-process dict which was lost on every
Render cold-start. The `session_turns` table is created automatically on first
use, piggy-backing on the existing aiosqlite connection path used by SQLAlchemy.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.config import get_settings

try:  # pragma: no cover - optional dependency
    import redis.asyncio as redis_async
except Exception:  # pragma: no cover - fallback
    redis_async = None

try:  # pragma: no cover - standard in aiosqlite env
    import aiosqlite
except Exception:  # pragma: no cover
    aiosqlite = None  # type: ignore[assignment]

settings = get_settings()

# ── SQLite path ──────────────────────────────────────────────────────────────
# Derive the SQLite file path from DATABASE_URL, e.g.
# "sqlite+aiosqlite:///./decision_intelligence.db"  →  "./decision_intelligence.db"
_DB_PATH: str = "./decision_intelligence.db"
_raw_url: str = getattr(settings, "database_url", "") or ""
if _raw_url.startswith("sqlite"):
    _path_part = _raw_url.split("///", 1)[-1]
    if _path_part:
        _DB_PATH = _path_part

_SQLITE_LOCK = asyncio.Lock()
_SQLITE_INIT_DONE = False


async def _ensure_table() -> None:
    """Create session_turns table if it doesn't exist (idempotent)."""
    global _SQLITE_INIT_DONE
    if _SQLITE_INIT_DONE or aiosqlite is None:
        return
    async with _SQLITE_LOCK:
        if _SQLITE_INIT_DONE:
            return
        try:
            async with aiosqlite.connect(_DB_PATH) as db:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS session_turns (
                        id        INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        question  TEXT NOT NULL,
                        insight   TEXT NOT NULL,
                        created_at REAL NOT NULL
                    )
                    """
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_session_turns_sid "
                    "ON session_turns (session_id)"
                )
                await db.commit()
            _SQLITE_INIT_DONE = True
        except Exception:
            pass  # Don't crash if DB unavailable; history just won't persist


async def _sqlite_get_history(session_id: str, ttl: int) -> list[str]:
    if aiosqlite is None:
        return []
    await _ensure_table()
    cutoff = time.time() - ttl
    try:
        async with aiosqlite.connect(_DB_PATH) as db:
            async with db.execute(
                "SELECT question, insight FROM session_turns "
                "WHERE session_id = ? AND created_at > ? ORDER BY id",
                (session_id, cutoff),
            ) as cursor:
                rows = await cursor.fetchall()
        return [f"Q: {q}\nA: {i}" for q, i in rows]
    except Exception:
        return []


async def _sqlite_add_entry(session_id: str, question: str, insight: str) -> None:
    if aiosqlite is None:
        return
    await _ensure_table()
    try:
        async with aiosqlite.connect(_DB_PATH) as db:
            await db.execute(
                "INSERT INTO session_turns (session_id, question, insight, created_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, question, insight, time.time()),
            )
            await db.commit()
            # Prune old entries for this session (keep last 20)
            await db.execute(
                """
                DELETE FROM session_turns
                WHERE session_id = ?
                  AND id NOT IN (
                      SELECT id FROM session_turns
                      WHERE session_id = ?
                      ORDER BY id DESC
                      LIMIT 20
                  )
                """,
                (session_id, session_id),
            )
            await db.commit()
    except Exception:
        pass


class SessionMemory:
    """Multi-turn conversational memory for a session.

    Storage priority: Redis → aiosqlite → (no-op if neither available).
    """

    DEFAULT_TTL = 86400  # 24 h

    def __init__(self, session_id: str) -> None:
        self.session_id = str(session_id)
        self._redis: Any = None

    async def _get_redis(self):
        if self._redis is not None:
            return self._redis
        if redis_async is None or not settings.redis_url:
            self._redis = False
            return None
        try:
            client = redis_async.from_url(settings.redis_url, decode_responses=True)
            await client.ping()
            self._redis = client
        except Exception:
            self._redis = False
        return None if self._redis is False else self._redis

    async def get_history(self) -> list[str]:
        """Return recent conversation turns as plain-text strings."""
        client = await self._get_redis()
        if client is not None:
            try:
                payload = await client.get(f"session:{self.session_id}")
                if payload:
                    items = json.loads(payload)
                    return [f"Q: {item['question']}\nA: {item['insight']}" for item in items]
                return []
            except Exception:
                pass  # Fall through to SQLite

        # Render's local disk is ephemeral. Production memory must be Redis
        # backed rather than silently creating an unencrypted local database.
        if get_settings().environment.lower() == "production":
            return []
        return await _sqlite_get_history(self.session_id, self.DEFAULT_TTL)

    async def add_entry(self, question: str, insight: str, ttl: int = DEFAULT_TTL) -> None:
        """Persist a new question/insight pair."""
        client = await self._get_redis()
        if client is not None:
            try:
                key = f"session:{self.session_id}"
                payload = await client.get(key)
                items = json.loads(payload) if payload else []
                items.append({"question": question, "insight": insight})
                # Keep last 20 turns
                items = items[-20:]
                await client.set(key, json.dumps(items), ex=ttl)
                return
            except Exception:
                pass  # Fall through to SQLite

        if get_settings().environment.lower() != "production":
            await _sqlite_add_entry(self.session_id, question, insight)
