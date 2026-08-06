"""
WebSocket endpoint:  ws://host/ws/orders

Streams order-lifecycle events (pending → filled → closed) to clients.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from shared.constants import RedisStream, WSChannel
from shared.redis_client import RedisClient

from .manager import ConnectionManager

logger = structlog.get_logger(__name__)

router = APIRouter()

CHANNEL = WSChannel.ORDERS
_GROUP = "api-ws-orders"
_CONSUMER = "api-orders-0"


@router.websocket("/ws/orders")
async def orders_ws(ws: WebSocket) -> None:
    manager: ConnectionManager = ws.app.state.ws_manager  # type: ignore[union-attr]
    await manager.connect(ws, CHANNEL)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        await logger.aexception("ws.orders.error")
    finally:
        await manager.disconnect(ws, CHANNEL)


async def order_broadcaster(redis: RedisClient, manager: ConnectionManager) -> None:
    await redis.ensure_consumer_group(RedisStream.ORDER_STREAM, _GROUP)
    async for event in redis.subscribe_stream(
        RedisStream.ORDER_STREAM, _GROUP, _CONSUMER,
    ):
        await manager.broadcast(CHANNEL, event.model_dump(mode="json"))
