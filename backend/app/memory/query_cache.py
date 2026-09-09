from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

from app.config import get_settings

try:  # pragma: no cover - optional dependency
    import redis.asyncio as redis_async
except Exception:  # pragma: no cover - fallback path
    redis_async = None

settings = get_settings()
_LOCAL_CACHE: dict[str, tuple[float, str]] = {}
_LOCK = asyncio.Lock()


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class QueryCache:
    def __init__(self) -> None:
        self._redis = None

    async def _get_redis(self):
        if self._redis is not None:
            return self._redis
        if redis_async is None or not settings.redis_url:
            self._redis = False
            return None
        try:
            self._redis = redis_async.from_url(settings.redis_url, decode_responses=True)
            await self._redis.ping()
        except Exception:
            self._redis = False
        return None if self._redis is False else self._redis

    async def get(self, key: str) -> str | None:
        hashed = _hash_key(key)
        client = await self._get_redis()
        if client is not None:
            try:
                return await client.get(hashed)
            except Exception:
                pass

        async with _LOCK:
            item = _LOCAL_CACHE.get(hashed)
            if not item:
                return None
            expires_at, value = item
            if time.time() > expires_at:
                _LOCAL_CACHE.pop(hashed, None)
                return None
            return value

    async def set(self, key: str, value: str, ttl: int = 300) -> None:
        hashed = _hash_key(key)
        client = await self._get_redis()
        if client is not None:
            try:
                await client.set(hashed, value, ex=ttl)
                return
            except Exception:
                pass

        async with _LOCK:
            now = time.time()
            for cache_key, (expires_at, _) in list(_LOCAL_CACHE.items()):
                if expires_at <= now:
                    _LOCAL_CACHE.pop(cache_key, None)
            max_entries = get_settings().max_local_cache_entries
            while len(_LOCAL_CACHE) >= max_entries:
                _LOCAL_CACHE.pop(next(iter(_LOCAL_CACHE)))
            _LOCAL_CACHE[hashed] = (time.time() + ttl, value)
