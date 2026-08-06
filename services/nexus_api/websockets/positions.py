"""
WebSocket endpoint:  ws://host/ws/positions

Pushes position-update events to connected dashboard clients.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from shared.constants import RedisStream, WSChannel
from shared.redis_client import RedisClient

from .manager import ConnectionManager

logger = structlog.get_logger(__name__)

router = APIRouter()

CHANNEL = WSChannel.POSITIONS
_GROUP = "api-ws-positions"
_CONSUMER = "api-positions-0"


@router.websocket("/ws/positions")
async def positions_ws(ws: WebSocket) -> None:
    manager: ConnectionManager = ws.app.state.ws_manager  # type: ignore[union-attr]
    await manager.connect(ws, CHANNEL)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        await logger.aexception("ws.positions.error")
    finally:
        await manager.disconnect(ws, CHANNEL)


async def position_broadcaster(redis: RedisClient, manager: ConnectionManager) -> None:
    await redis.ensure_consumer_group(RedisStream.POSITION_UPDATE, _GROUP)
    async for event in redis.subscribe_stream(
        RedisStream.POSITION_UPDATE, _GROUP, _CONSUMER,
    ):
        await manager.broadcast(CHANNEL, event.model_dump(mode="json"))
