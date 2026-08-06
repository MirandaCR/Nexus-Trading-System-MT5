"""
Generic WebSocket ConnectionManager used by every WS endpoint.

Each *channel* (e.g. "ticks", "agents") maintains its own set of connected
clients.  A client can optionally subscribe to a subset of *topics* within a
channel (e.g. symbol names for the ticks channel).
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

import structlog
from fastapi import WebSocket

logger = structlog.get_logger(__name__)


class ConnectionManager:
    """Manages WebSocket connections grouped by channel and optional topics."""

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._subscriptions: dict[str, dict[WebSocket, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        self._lock = asyncio.Lock()

    async def connect(
        self,
        ws: WebSocket,
        channel: str,
        topics: set[str] | None = None,
    ) -> None:
        await ws.accept()
        async with self._lock:
            self._connections[channel].add(ws)
            if topics:
                self._subscriptions[channel][ws] = topics
        await logger.adebug("ws.connect", channel=channel, topics=topics)

    async def disconnect(self, ws: WebSocket, channel: str) -> None:
        async with self._lock:
            self._connections[channel].discard(ws)
            self._subscriptions[channel].pop(ws, None)
        await logger.adebug("ws.disconnect", channel=channel)

    async def subscribe(self, ws: WebSocket, channel: str, topics: set[str]) -> None:
        async with self._lock:
            self._subscriptions[channel][ws] |= topics

    async def unsubscribe(self, ws: WebSocket, channel: str, topics: set[str]) -> None:
        async with self._lock:
            subs = self._subscriptions[channel].get(ws)
            if subs:
                subs -= topics

    async def broadcast(
        self,
        channel: str,
        data: dict[str, Any],
        *,
        topic: str | None = None,
    ) -> None:
        """Send ``data`` to every client on *channel*.

        If *topic* is given, only clients subscribed to that topic (or clients
        with **no** topic filter) receive the message.
        """
        payload = json.dumps(data, default=str)
        async with self._lock:
            targets = list(self._connections.get(channel, set()))

        stale: list[WebSocket] = []
        for ws in targets:
            if topic:
                subs = self._subscriptions[channel].get(ws)
                if subs and topic not in subs:
                    continue
            try:
                await ws.send_text(payload)
            except Exception:
                stale.append(ws)

        for ws in stale:
            await self.disconnect(ws, channel)

    async def send_personal(self, ws: WebSocket, data: dict[str, Any]) -> None:
        try:
            await ws.send_json(data)
        except Exception:
            pass

    def active_count(self, channel: str) -> int:
        return len(self._connections.get(channel, set()))
