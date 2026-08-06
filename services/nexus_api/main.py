"""
Nexus Trader — API Gateway.

Central HTTP + WebSocket gateway that the dashboard (and external clients)
talk to.  Every other micro-service is reachable only through this gateway
or through Redis Streams.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from shared.constants import SERVICE_PORTS

from .deps import config_loader, duckdb, redis, ws_manager
from .routers import (
    agents,
    backtest,
    config,
    intelligence,
    intermarket,
    market,
    ml,
    news,
    notifications,
    performance,
    positions,
    regime,
    risk,
    strategies,
    system,
    trades,
)
from .websockets import (
    agents as ws_agents,
    news as ws_news,
    orders as ws_orders,
    positions as ws_positions,
    risk as ws_risk,
    ticks as ws_ticks,
)

from shared import setup_logger as _setup_logger
_setup_logger("nexus-api")
logger = structlog.get_logger(__name__)

_broadcaster_tasks: list[asyncio.Task] = []  # type: ignore[type-arg]


async def _start_ws_broadcasters() -> None:
    from .websockets.ticks import tick_broadcaster
    from .websockets.agents import agent_broadcaster
    from .websockets.positions import position_broadcaster
    from .websockets.orders import order_broadcaster
    from .websockets.news import news_broadcaster
    from .websockets.risk import risk_broadcaster

    broadcasters = [
        tick_broadcaster(redis, ws_manager),
        agent_broadcaster(redis, ws_manager),
        position_broadcaster(redis, ws_manager),
        order_broadcaster(redis, ws_manager),
        news_broadcaster(redis, ws_manager),
        risk_broadcaster(redis, ws_manager),
    ]
    for coro in broadcasters:
        _broadcaster_tasks.append(asyncio.create_task(coro))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.ws_manager = ws_manager
    app.state.duckdb_available = False
    await redis.connect()
    try:
        await duckdb.init_db()
        app.state.duckdb_available = True
    except Exception as exc:
        await logger.awarning(
            "api_gateway.duckdb_unavailable",
            error=str(exc),
        )
    config_loader.load_all()
    await _start_ws_broadcasters()
    await logger.ainfo("api_gateway.started", port=SERVICE_PORTS["nexus-api"])
    yield
    for task in _broadcaster_tasks:
        task.cancel()
    await asyncio.gather(*_broadcaster_tasks, return_exceptions=True)
    _broadcaster_tasks.clear()
    await redis.disconnect()
    await duckdb.close()
    await logger.ainfo("api_gateway.stopped")


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Nexus Trader API Gateway",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

DASHBOARD_ORIGIN = os.getenv("DASHBOARD_ORIGIN", "http://localhost:3100")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[DASHBOARD_ORIGIN, "http://localhost:3100", "http://127.0.0.1:3100"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── REST routers ─────────────────────────────────────────────────────────────

app.include_router(positions.router)
app.include_router(trades.router)
app.include_router(performance.router)
app.include_router(agents.router)
app.include_router(risk.router)
app.include_router(system.router)
app.include_router(news.router)
app.include_router(intermarket.router)
app.include_router(config.router)
app.include_router(market.router)
app.include_router(strategies.router)
app.include_router(ml.router)
app.include_router(regime.router)
app.include_router(intelligence.router)
app.include_router(notifications.router)
app.include_router(backtest.router)

# ── WebSocket routers ────────────────────────────────────────────────────────

app.include_router(ws_ticks.router)
app.include_router(ws_agents.router)
app.include_router(ws_positions.router)
app.include_router(ws_orders.router)
app.include_router(ws_news.router)
app.include_router(ws_risk.router)


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/health", tags=["health"])
async def health_check() -> dict:
    redis_ok = await redis.health_check()
    return {
        "status": "healthy" if redis_ok else "degraded",
        "service": "nexus-api",
        "redis": "connected" if redis_ok else "disconnected",
        "duckdb": "connected" if app.state.duckdb_available else "unavailable",
    }


# ── Global error handler ────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    await logger.aexception(
        "unhandled_error", path=request.url.path, error=str(exc),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
