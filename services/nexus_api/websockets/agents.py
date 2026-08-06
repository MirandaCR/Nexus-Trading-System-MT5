"""
WebSocket endpoint:  ws://host/ws/agents

Broadcasts all agent analysis and verdict messages in real-time.
"""
from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from shared.constants import RedisStream, WSChannel
from shared.redis_client import RedisClient

from .manager import ConnectionManager

logger = structlog.get_logger(__name__)

router = APIRouter()

CHANNEL = WSChannel.AGENT_MESSAGES
_GROUP = "api-ws-agents"
_CONSUMER_ANALYSIS = "api-agents-analysis-0"
_CONSUMER_VERDICT = "api-agents-verdict-0"


@router.websocket("/ws/agents")
async def agents_ws(ws: WebSocket) -> None:
    manager: ConnectionManager = ws.app.state.ws_manager  # type: ignore[union-attr]
    await manager.connect(ws, CHANNEL)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        await logger.aexception("ws.agents.error")
    finally:
        await manager.disconnect(ws, CHANNEL)


async def agent_broadcaster(redis: RedisClient, manager: ConnectionManager) -> None:
    """Relay both analysis and verdict streams to connected WS clients."""
    await asyncio.gather(
        _relay_stream(redis, manager, RedisStream.AGENT_ANALYSIS, _CONSUMER_ANALYSIS),
        _relay_stream(redis, manager, RedisStream.AGENT_VERDICT, _CONSUMER_VERDICT),
    )


async def _relay_stream(
    redis: RedisClient,
    manager: ConnectionManager,
    stream: RedisStream,
    consumer: str,
) -> None:
    await redis.ensure_consumer_group(stream, _GROUP)
    async for event in redis.subscribe_stream(stream, _GROUP, consumer):
        await manager.broadcast(CHANNEL, event.model_dump(mode="json"))
