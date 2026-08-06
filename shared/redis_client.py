"""
Nexus Trader — async Redis client.

Thin wrapper around ``redis.asyncio`` providing:
  * connection pooling
  * Redis-Stream publish / consume helpers
  * cache get / set with TTL
  * health-check
  * structured logging via structlog
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator

import redis.asyncio as aioredis
import structlog

from .constants import REDIS_KEY_PREFIX, STREAM_MAX_LEN, RedisStream
from .events import Event, parse_event

logger = structlog.get_logger(__name__)


class RedisClient:
    """Async Redis client with stream and cache helpers."""

    def __init__(
        self,
        url: str | None = None,
        *,
        max_connections: int = 20,
        decode_responses: bool = True,
    ) -> None:
        # Use 127.0.0.1 (not "localhost") by default: on Windows "localhost"
        # resolves to IPv6 ::1 first, and if Redis only listens on IPv4 each new
        # connection eats a ~2s timeout before falling back. Forcing IPv4 avoids
        # that penalty entirely.
        self._url = url or os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
        self._max_connections = max_connections
        self._decode_responses = decode_responses
        self._pool: aioredis.ConnectionPool | None = None
        self._redis: aioredis.Redis | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def connect(self) -> None:
        if self._redis is not None:
            return
        self._pool = aioredis.ConnectionPool.from_url(
            self._url,
            max_connections=self._max_connections,
            decode_responses=self._decode_responses,
        )
        self._redis = aioredis.Redis(connection_pool=self._pool)
        await logger.ainfo("redis.connected", url=self._url)

    async def disconnect(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
        if self._pool is not None:
            await self._pool.disconnect()
            self._pool = None
        await logger.ainfo("redis.disconnected")

    @property
    def r(self) -> aioredis.Redis:
        if self._redis is None:
            raise RuntimeError("RedisClient is not connected — call connect() first")
        return self._redis

    # ── Health ───────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        try:
            return await self.r.ping()
        except Exception:
            await logger.awarning("redis.health_check_failed")
            return False

    # ── Stream Publish ───────────────────────────────────────────────────

    async def publish_event(
        self,
        stream: str | RedisStream,
        event: Event,
        *,
        maxlen: int = STREAM_MAX_LEN,
    ) -> str:
        """Publish an event to a Redis Stream. Returns the stream entry ID."""
        payload = event.to_stream_dict()
        entry_id: str = await self.r.xadd(
            str(stream),
            payload,
            maxlen=maxlen,
            approximate=True,
        )
        await logger.adebug(
            "redis.stream.published",
            stream=str(stream),
            event_type=event.event_type,
            entry_id=entry_id,
        )
        return entry_id

    # ── Stream Consume ───────────────────────────────────────────────────

    async def ensure_consumer_group(
        self,
        stream: str | RedisStream,
        group: str,
    ) -> None:
        try:
            await self.r.xgroup_create(
                str(stream), group, id="0", mkstream=True,
            )
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def subscribe_stream(
        self,
        stream: str | RedisStream,
        group: str,
        consumer: str,
        *,
        count: int = 10,
        block_ms: int = 2000,
    ) -> AsyncIterator[Event]:
        """Yield parsed Events from a consumer-group read loop."""
        stream_key = str(stream)
        await self.ensure_consumer_group(stream_key, group)

        while True:
            try:
                entries = await self.r.xreadgroup(
                    groupname=group,
                    consumername=consumer,
                    streams={stream_key: ">"},
                    count=count,
                    block=block_ms,
                )
                if not entries:
                    continue
                for _stream_name, messages in entries:
                    for msg_id, fields in messages:
                        try:
                            event = parse_event(fields)
                            yield event
                        except Exception:
                            await logger.awarning(
                                "redis.stream.parse_error",
                                stream=stream_key,
                                msg_id=msg_id,
                            )
                        finally:
                            await self.r.xack(stream_key, group, msg_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                await logger.aexception("redis.stream.read_error", stream=stream_key)
                await asyncio.sleep(1)

    # ── Cache ────────────────────────────────────────────────────────────

    async def cache_set(
        self,
        key: str,
        value: Any,
        *,
        ttl_sec: int = 60,
        prefix: bool = True,
    ) -> None:
        full_key = f"{REDIS_KEY_PREFIX}{key}" if prefix else key
        serialised = json.dumps(value, default=str)
        await self.r.set(full_key, serialised, ex=ttl_sec)

    async def cache_get(
        self,
        key: str,
        *,
        prefix: bool = True,
    ) -> Any | None:
        full_key = f"{REDIS_KEY_PREFIX}{key}" if prefix else key
        raw = await self.r.get(full_key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    async def cache_delete(self, key: str, *, prefix: bool = True) -> None:
        full_key = f"{REDIS_KEY_PREFIX}{key}" if prefix else key
        await self.r.delete(full_key)

    # ── Pub/Sub (simple channel-based) ───────────────────────────────────

    async def publish_channel(self, channel: str, data: dict[str, Any]) -> None:
        await self.r.publish(channel, json.dumps(data, default=str))

    async def subscribe_channel(
        self, *channels: str
    ) -> AsyncIterator[dict[str, Any]]:
        pubsub = self.r.pubsub()
        await pubsub.subscribe(*channels)
        try:
            async for msg in pubsub.listen():
                if msg["type"] == "message":
                    try:
                        yield json.loads(msg["data"])
                    except json.JSONDecodeError:
                        yield {"raw": msg["data"]}
        finally:
            await pubsub.unsubscribe(*channels)
            await pubsub.aclose()
