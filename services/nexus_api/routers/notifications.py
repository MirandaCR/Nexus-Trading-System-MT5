"""
Notifications Router — Server-Sent Events stream for real-time alerts.

Endpoint:
  GET /api/notifications/stream   — SSE stream; sends alert log on connect then live events
  GET /api/notifications/log      — last N alerts (JSON)

Alert format:
  {
    "type":     "BLACKOUT_START" | "BLACKOUT_END" | "BRIEF_READY" |
                "CORR_DIVERGENCE" | "SIGNAL_HIGH" | "CIRCUIT_BREAKER",
    "message":  "<human readable>",
    "severity": "HIGH" | "MEDIUM" | "INFO",
    "ts":       "<ISO 8601>",
    ...extra keys per type
  }

Services write alerts by:
  1. LPUSH to nexus:alerts:log  (capped at 50, TTL 24h)
  2. PUBLISH to nexus:alerts:pubsub
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

import redis.asyncio as aioredis
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

REDIS_URL      = os.getenv("REDIS_URL", "redis://localhost:6379")
ALERT_LOG_KEY  = "nexus:alerts:log"
ALERT_CHAN     = "nexus:alerts:pubsub"
LOG_MAX        = 50
KEEPALIVE_SEC  = 20   # send keepalive comment every N seconds

_redis: aioredis.Redis | None = None

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True, max_connections=5)
    return _redis


@router.get("/stream")
async def notification_stream(request: Request, prefetch: int = 10):
    """SSE stream — sends the last `prefetch` alerts immediately then live events."""

    async def generator():
        # 1. Prefetch recent alerts from log
        try:
            r = await _get_redis()
            backlog = await r.lrange(ALERT_LOG_KEY, 0, prefetch - 1)
            for raw in reversed(backlog):   # oldest first
                yield f"data: {raw}\n\n"
        except Exception as exc:
            logger.debug("notifications.prefetch_error: %s", exc)

        # 2. Subscribe to live channel with its own dedicated connection
        pubsub_conn = aioredis.from_url(REDIS_URL, decode_responses=True)
        pubsub = pubsub_conn.pubsub()
        await pubsub.subscribe(ALERT_CHAN)

        last_keepalive = asyncio.get_event_loop().time()
        try:
            while True:
                if await request.is_disconnected():
                    break

                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=0.5
                )
                if message and message.get("type") == "message":
                    yield f"data: {message['data']}\n\n"

                # Keepalive comment to prevent proxy/browser timeouts
                now = asyncio.get_event_loop().time()
                if now - last_keepalive >= KEEPALIVE_SEC:
                    yield ": keepalive\n\n"
                    last_keepalive = now

                await asyncio.sleep(0.05)

        except asyncio.CancelledError:
            pass
        finally:
            try:
                await pubsub.unsubscribe(ALERT_CHAN)
                await pubsub.aclose()
                await pubsub_conn.aclose()
            except Exception:
                pass

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/log")
async def get_alert_log(limit: int = 20):
    """Return the last `limit` alerts from the log (newest first)."""
    try:
        r = await _get_redis()
        raw = await r.lrange(ALERT_LOG_KEY, 0, min(limit, LOG_MAX) - 1)
        alerts = []
        for item in raw:
            try:
                alerts.append(json.loads(item))
            except json.JSONDecodeError:
                pass
        return {"alerts": alerts, "count": len(alerts)}
    except Exception:
        return {"alerts": [], "count": 0, "error": "redis unavailable"}
