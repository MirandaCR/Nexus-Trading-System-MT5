"""
Performance router — proxies to nexus-data (DuckDB owner).
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from ..http_client import new_client
import structlog
from fastapi import APIRouter, Query

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/performance", tags=["performance"])

_DATA_URL = os.getenv("NEXUS_DATA_URL", "http://localhost:8001")

_EMPTY_PERF = {
    "total_pnl": 0.0, "win_rate": 0.0, "profit_factor": 0.0,
    "sharpe_ratio": 0.0, "max_drawdown": 0.0, "total_trades": 0,
    "avg_win": 0.0, "avg_loss": 0.0,
}


async def _get(path: str, params: dict | None = None) -> Any:
    try:
        async with new_client(timeout=10.0) as client:
            resp = await client.get(f"{_DATA_URL}{path}", params=params or {})
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("performance.proxy.error", path=path, error=str(exc))
        return None


@router.get("")
async def overall_performance() -> dict[str, Any]:
    data = await _get("/api/data/performance")
    return data if data else _EMPTY_PERF


@router.get("/equity-curve")
async def equity_curve(limit: int = Query(200, ge=1, le=2000)) -> list[dict[str, Any]]:
    data = await _get("/api/data/performance/equity-curve", {"limit": limit})
    return data if data else []


@router.get("/daily")
async def daily_pnl(since: str | None = Query(None)) -> list[dict[str, Any]]:
    params = {"since": since} if since else {}
    data = await _get("/api/data/performance/daily", params)
    return data if data else []


@router.get("/by-session")
async def performance_by_session() -> list[dict[str, Any]]:
    data = await _get("/api/data/performance/by-session")
    return data if data else []


@router.get("/by-symbol")
async def performance_by_symbol() -> list[dict[str, Any]]:
    data = await _get("/api/data/performance/by-symbol")
    return data if data else []
