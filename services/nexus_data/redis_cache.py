"""
Redis tick cache and event publisher.

Hot ticks live in Redis sorted sets (scored by timestamp) so any consumer
can retrieve the *N* most recent quotes for a symbol in O(log N).
Tick events are also pushed into a Redis Stream for downstream consumers.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from shared.constants import REDIS_KEY_PREFIX, RedisStream, STREAM_MAX_LEN
from shared.events import TickEvent
from shared.models import Tick
from shared.redis_client import RedisClient

log = structlog.get_logger("nexus_data.redis_cache")

TICK_KEY_PREFIX = f"{REDIS_KEY_PREFIX}tick:"
TICK_LATEST_PREFIX = f"{REDIS_KEY_PREFIX}tick:latest:"
TICK_HISTORY_MAX = 1000
TICK_TTL_SEC = 300  # 5 minutes


class TickCache:
    """Manages a per-symbol sorted-set of recent ticks in Redis."""

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    # ── Write ────────────────────────────────────────────────────────────

    async def cache_tick(self, tick: Tick) -> None:
        """Store *tick* in both a sorted set (history) and a simple key (latest)."""
        r = self._redis.r
        history_key = f"{TICK_KEY_PREFIX}{tick.symbol}"
        latest_key = f"{TICK_LATEST_PREFIX}{tick.symbol}"

        payload = tick.model_dump_json()
        score = tick.timestamp.timestamp()

        pipe = r.pipeline(transaction=False)
        # Sorted set for history
        pipe.zadd(history_key, {payload: score})
        pipe.zremrangebyrank(history_key, 0, -(TICK_HISTORY_MAX + 1))
        pipe.expire(history_key, TICK_TTL_SEC)
        # Latest tick (simple key)
        pipe.set(latest_key, payload, ex=TICK_TTL_SEC)
        await pipe.execute()

    async def publish_tick_event(self, tick: Tick) -> None:
        """Publish a tick event to the Redis Stream for downstream services."""
        event = TickEvent(
            source="nexus-data",
            symbol=tick.symbol,
            bid=tick.bid,
            ask=tick.ask,
            spread=tick.spread,
        )
        await self._redis.publish_event(
            RedisStream.TICK_STREAM, event, maxlen=STREAM_MAX_LEN,
        )

    # ── Read ─────────────────────────────────────────────────────────────

    async def get_latest_tick(self, symbol: str) -> Tick | None:
        """Return the single most recent tick for *symbol*, or ``None``."""
        raw = await self._redis.r.get(f"{TICK_LATEST_PREFIX}{symbol}")
        if raw is None:
            return None
        return Tick.model_validate_json(raw)

    async def get_tick_history(
        self, symbol: str, count: int = 50,
    ) -> list[Tick]:
        """Return up to *count* most recent ticks (newest first)."""
        history_key = f"{TICK_KEY_PREFIX}{symbol}"
        raw_items: list[bytes | str] = await self._redis.r.zrevrange(
            history_key, 0, count - 1,
        )
        return [Tick.model_validate_json(item) for item in raw_items]

    # ── Cache invalidation ───────────────────────────────────────────────

    async def invalidate(self, symbol: str) -> None:
        """Remove all cached data for *symbol*."""
        r = self._redis.r
        await r.delete(
            f"{TICK_KEY_PREFIX}{symbol}",
            f"{TICK_LATEST_PREFIX}{symbol}",
        )
        await log.ainfo("cache.invalidated", symbol=symbol)

    async def invalidate_all(self) -> None:
        """Flush every tick cache key (use sparingly)."""
        r = self._redis.r
        async for key in r.scan_iter(match=f"{TICK_KEY_PREFIX}*"):
            await r.delete(key)
        async for key in r.scan_iter(match=f"{TICK_LATEST_PREFIX}*"):
            await r.delete(key)
        await log.ainfo("cache.invalidated_all")
