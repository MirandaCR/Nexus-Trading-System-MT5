"""
WebSocket endpoint:  ws://host/ws/risk

Pushes risk-state changes (circuit breakers, drawdown alerts, tilt) to clients.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from shared.constants import RedisStream, WSChannel
from shared.redis_client import RedisClient

from .manager import ConnectionManager

logger = structlog.get_logger(__name__)

router = APIRouter()

CHANNEL = WSChannel.RISK_STATE
_GROUP = "api-ws-risk"
_CONSUMER = "api-risk-0"


@router.websocket("/ws/risk")
async def risk_ws(ws: WebSocket) -> None:
    manager: ConnectionManager = ws.app.state.ws_manager  # type: ignore[union-attr]
    await manager.connect(ws, CHANNEL)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        await logger.aexception("ws.risk.error")
    finally:
        await manager.disconnect(ws, CHANNEL)


async def risk_broadcaster(redis: RedisClient, manager: ConnectionManager) -> None:
    await redis.ensure_consumer_group(RedisStream.RISK_DECISION, _GROUP)
    async for event in redis.subscribe_stream(
        RedisStream.RISK_DECISION, _GROUP, _CONSUMER,
    ):
        await manager.broadcast(CHANNEL, event.model_dump(mode="json"))
