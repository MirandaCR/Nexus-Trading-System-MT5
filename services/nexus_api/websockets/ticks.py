"""
WebSocket endpoint:  ws://host/ws/ticks

Clients send a JSON subscribe/unsubscribe message to pick symbols::

    {"action": "subscribe",   "symbols": ["EURUSD", "GBPUSD"]}
    {"action": "unsubscribe", "symbols": ["GBPUSD"]}

A background broadcaster reads the Redis tick stream and fans out to all
connected clients whose subscriptions match.
"""
from __future__ import annotations

import asyncio
import json

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from shared.constants import RedisStream, WSChannel
from shared.redis_client import RedisClient

from .manager import ConnectionManager

logger = structlog.get_logger(__name__)

router = APIRouter()

CHANNEL = WSChannel.TICKS
_GROUP = "api-ws-ticks"
_CONSUMER = "api-ticks-0"


async def _handle_ticks_socket(ws: WebSocket) -> None:
    manager: ConnectionManager = ws.app.state.ws_manager  # type: ignore[union-attr]
    await manager.connect(ws, CHANNEL)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_personal(ws, {"error": "invalid JSON"})
                continue
            action = msg.get("action")
            symbols = set(msg.get("symbols", []))
            if action == "subscribe" and symbols:
                await manager.subscribe(ws, CHANNEL, symbols)
                await manager.send_personal(ws, {"subscribed": sorted(symbols)})
            elif action == "unsubscribe" and symbols:
                await manager.unsubscribe(ws, CHANNEL, symbols)
                await manager.send_personal(ws, {"unsubscribed": sorted(symbols)})
            else:
                await manager.send_personal(ws, {"error": "unknown action"})
    except WebSocketDisconnect:
        pass
    except Exception:
        await logger.aexception("ws.ticks.error")
    finally:
        await manager.disconnect(ws, CHANNEL)


@router.websocket("/ws/ticks")
async def ticks_ws(ws: WebSocket) -> None:
    await _handle_ticks_socket(ws)


@router.websocket("/ws/stream")
async def ticks_stream_alias(ws: WebSocket) -> None:
    """Backward-compatible path used by some dashboards / older clients."""
    await _handle_ticks_socket(ws)


async def tick_broadcaster(redis: RedisClient, manager: ConnectionManager) -> None:
    await redis.ensure_consumer_group(RedisStream.TICK_STREAM, _GROUP)
    async for event in redis.subscribe_stream(
        RedisStream.TICK_STREAM, _GROUP, _CONSUMER,
    ):
        data = event.model_dump(mode="json")
        symbol = getattr(event, "symbol", None)
        await manager.broadcast(CHANNEL, data, topic=symbol)
