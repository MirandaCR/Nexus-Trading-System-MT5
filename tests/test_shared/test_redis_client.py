"""Tests for shared.redis_client — cache and stream helpers."""
from __future__ import annotations

import pytest
import pytest_asyncio

from shared.redis_client import RedisClient


class TestRedisClientCache:
    @pytest.mark.asyncio
    async def test_cache_set_and_get(self, redis_client: RedisClient):
        await redis_client.cache_set("test:key", {"value": 42})
        result = await redis_client.cache_get("test:key")
        assert result == {"value": 42}

    @pytest.mark.asyncio
    async def test_cache_get_missing_key(self, redis_client: RedisClient):
        result = await redis_client.cache_get("nonexistent:key")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_delete(self, redis_client: RedisClient):
        await redis_client.cache_set("del:key", "value")
        await redis_client.cache_delete("del:key")
        result = await redis_client.cache_get("del:key")
        assert result is None


class TestRedisClientStream:
    @pytest.mark.asyncio
    async def test_publish_event(self, redis_client: RedisClient):
        from shared.events import TickEvent

        event = TickEvent(symbol="EURUSD", bid=1.085, ask=1.0852)
        entry_id = await redis_client.publish_event("stream:test", event)
        assert entry_id is not None

    @pytest.mark.asyncio
    async def test_ensure_consumer_group(self, redis_client: RedisClient):
        await redis_client.ensure_consumer_group("stream:test", "test-group")


class TestRedisClientHealth:
    @pytest.mark.asyncio
    async def test_health_check(self, redis_client: RedisClient):
        healthy = await redis_client.health_check()
        assert healthy is True

    @pytest.mark.asyncio
    async def test_not_connected_raises(self):
        client = RedisClient(url="redis://localhost:9999/0")
        with pytest.raises(RuntimeError, match="not connected"):
            _ = client.r
