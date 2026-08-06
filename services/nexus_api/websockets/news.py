"""
WebSocket endpoint:  ws://host/ws/news

Pushes high-impact news alerts to dashboard clients in real-time.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from shared.constants import RedisStream, WSChannel
from shared.redis_client import RedisClient

from .manager import ConnectionManager

logger = structlog.get_logger(__name__)

router = APIRouter()

CHANNEL = WSChannel.NEWS
_GROUP = "api-ws-news"
_CONSUMER = "api-news-0"


@router.websocket("/ws/news")
async def news_ws(ws: WebSocket) -> None:
    manager: ConnectionManager = ws.app.state.ws_manager  # type: ignore[union-attr]
    await manager.connect(ws, CHANNEL)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        await logger.aexception("ws.news.error")
    finally:
        await manager.disconnect(ws, CHANNEL)


async def news_broadcaster(redis: RedisClient, manager: ConnectionManager) -> None:
    await redis.ensure_consumer_group(RedisStream.NEWS_ALERT, _GROUP)
    async for event in redis.subscribe_stream(
        RedisStream.NEWS_ALERT, _GROUP, _CONSUMER,
    ):
        await manager.broadcast(CHANNEL, event.model_dump(mode="json"))
