"""
Shared pytest fixtures for the Nexus Trader test suite.

Provides mock Redis, DuckDB, agent state factories, and HTTP client helpers
so individual test modules stay lean.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from shared.constants import AgentID, SessionName, Verdict
from shared.models import (
    OHLCV,
    AgentMessage,
    CircuitBreaker,
    NewsItem,
    PerformanceMetrics,
    Position,
    RiskState,
    SessionState,
    Tick,
    Trade,
    TradeProposal,
)
from shared.redis_client import RedisClient


# ── Async loop ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── Mock Redis ────────────────────────────────────────────────────────────────

class FakeRedis:
    """In-memory Redis mock supporting streams, cache, and pub/sub."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self._streams: dict[str, list[dict[str, str]]] = {}
        self._groups: dict[str, set[str]] = {}

    async def ping(self) -> bool:
        return True

    async def xadd(self, stream: str, fields: dict, *, maxlen: int = 10000, approximate: bool = True) -> str:
        self._streams.setdefault(stream, []).append(fields)
        return f"0-{len(self._streams[stream])}"

    async def xgroup_create(self, stream: str, group: str, id: str = "0", mkstream: bool = True) -> None:
        self._groups.setdefault(stream, set()).add(group)

    async def xreadgroup(self, *, groupname: str, consumername: str, streams: dict, count: int = 10, block: int = 0):
        return []

    async def xack(self, stream: str, group: str, msg_id: str) -> int:
        return 1

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._cache[key] = value

    async def get(self, key: str) -> str | None:
        return self._cache.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self._cache.pop(key, None) else 0

    async def publish(self, channel: str, message: str) -> int:
        return 1

    def pubsub(self):
        return MagicMock()

    async def aclose(self) -> None:
        pass


@pytest.fixture()
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest_asyncio.fixture()
async def redis_client(fake_redis: FakeRedis) -> AsyncIterator[RedisClient]:
    client = RedisClient.__new__(RedisClient)
    client._url = "redis://localhost:6379/0"
    client._max_connections = 5
    client._decode_responses = True
    client._pool = None
    client._redis = fake_redis  # type: ignore[assignment]
    yield client


# ── Agent State Factory ──────────────────────────────────────────────────────

@pytest.fixture()
def make_agent_state():
    """Return a factory that builds a fresh AgentState dict."""

    def _factory(
        symbol: str = "EURUSD",
        timeframe: str = "M15",
        session: str = "LONDON",
        **overrides: Any,
    ) -> dict[str, Any]:
        state: dict[str, Any] = {
            "symbol": symbol,
            "timeframe": timeframe,
            "session": session,
            "ml_signal": {},
            "features": {},
            "technical_analysis": "",
            "news_assessment": "",
            "intermarket_score": 0.0,
            "flow_score": 0.0,
            "bull_case": "",
            "bull_conviction": 0,
            "bear_case": "",
            "bear_conviction": 0,
            "verdict": "NO_TRADE",
            "verdict_conviction": 0,
            "verdict_reasoning": "",
            "risk_decision": "REJECT",
            "position_size": 0.0,
            "risk_reasoning": "",
            "order": None,
            "messages": [],
            "cycle_id": uuid.uuid4().hex[:12],
            "errors": [],
        }
        state.update(overrides)
        return state

    return _factory


# ── Model Factories ──────────────────────────────────────────────────────────

@pytest.fixture()
def sample_tick() -> Tick:
    return Tick(symbol="EURUSD", bid=1.08500, ask=1.08520)


@pytest.fixture()
def sample_ohlcv() -> OHLCV:
    return OHLCV(
        symbol="EURUSD",
        timeframe="M15",
        open=1.08450,
        high=1.08600,
        low=1.08400,
        close=1.08550,
        volume=1200.0,
    )


@pytest.fixture()
def sample_position() -> Position:
    return Position(
        symbol="EURUSD",
        side=Verdict.BUY,
        entry_price=1.08500,
        current_price=1.08550,
        sl=1.08350,
        tp1=1.08700,
        size=0.1,
        pnl_pips=5.0,
        pnl_usd=50.0,
    )


@pytest.fixture()
def sample_trade() -> Trade:
    return Trade(
        symbol="EURUSD",
        side=Verdict.BUY,
        entry_price=1.08500,
        exit_price=1.08700,
        pnl_pips=20.0,
        pnl_usd=200.0,
        rr_ratio=2.0,
        duration=600.0,
        session=SessionName.LONDON,
        conviction=0.85,
    )


@pytest.fixture()
def sample_proposal() -> TradeProposal:
    return TradeProposal(
        symbol="EURUSD",
        direction=Verdict.BUY,
        entry=1.08500,
        sl=1.08350,
        tp1=1.08700,
        tp2=1.08900,
        tp3=1.09100,
        conviction=0.82,
        reasoning="Strong bullish momentum with HTF alignment",
    )


@pytest.fixture()
def sample_risk_state() -> RiskState:
    return RiskState(
        daily_loss_pct=1.5,
        weekly_dd_pct=3.0,
        open_positions=1,
        correlation_exposure=0.3,
        tilt_status="normal",
    )


@pytest.fixture()
def sample_news_item() -> NewsItem:
    return NewsItem(
        headline="Fed holds rates steady",
        source="Reuters",
        impact="high",
        sentiment="neutral",
        affected_currencies=["USD"],
        finbert_score=0.05,
    )


# ── HTTP Test Client (FastAPI) ───────────────────────────────────────────────

@pytest_asyncio.fixture()
async def api_client(redis_client: RedisClient) -> AsyncIterator[AsyncClient]:
    """Build an AsyncClient wired to the nexus-api app with mocked deps."""
    from services.nexus_api.main import create_app
    from services.nexus_api.deps import get_redis, get_duckdb

    app = create_app()
    mock_db = AsyncMock()
    mock_db.query = AsyncMock(return_value=[])

    app.dependency_overrides[get_redis] = lambda: redis_client
    app.dependency_overrides[get_duckdb] = lambda: mock_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
