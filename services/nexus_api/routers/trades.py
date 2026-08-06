"""
Trades router — proxies to nexus-data which owns the DuckDB write lock.

On Windows, DuckDB enforces an exclusive write lock: the process that opened
the file for writing cannot coexist with a read-only opener in another process.
nexus-data is the sole DuckDB writer, so all trade queries go through it.
"""
from __future__ import annotations

from typing import Any

import httpx

from ..http_client import new_client
import structlog
from fastapi import APIRouter, HTTPException, Query, status

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/trades", tags=["trades"])

import os
_DATA_URL = os.getenv("NEXUS_DATA_URL", "http://localhost:8001")


async def _proxy_trades(params: dict[str, Any]) -> dict[str, Any]:
    """Forward a trades query to nexus-data and return its JSON response."""
    clean = {k: v for k, v in params.items() if v is not None}
    try:
        async with new_client(timeout=10.0) as client:
            resp = await client.get(f"{_DATA_URL}/api/data/trades", params=clean)
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        logger.warning("trades.proxy.nexus_data_unreachable")
        return {"trades": [], "total": 0, "page": 1, "page_size": clean.get("page_size", 50)}
    except Exception as exc:
        logger.warning("trades.proxy.error", error=str(exc))
        return {"trades": [], "total": 0, "page": 1, "page_size": clean.get("page_size", 50)}


@router.get("")
async def list_trades(
    symbol: str | None = Query(None),
    session: str | None = Query(None),
    side: str | None = Query(None),
    since: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    data = await _proxy_trades(
        {"symbol": symbol, "session": session, "since": since,
         "page": page, "page_size": page_size}
    )
    # Apply side filter client-side (nexus-data doesn't support it yet)
    if side and data.get("trades"):
        data["trades"] = [
            t for t in data["trades"]
            if str(t.get("side", "")).upper() == side.upper()
        ]
        data["total"] = len(data["trades"])
    return data


@router.get("/stats")
async def trade_stats(
    symbol: str | None = Query(None),
    session: str | None = Query(None),
    since: str | None = Query(None),
) -> dict[str, Any]:
    data = await _proxy_trades(
        {"symbol": symbol, "session": session, "since": since, "page": 1, "page_size": 200}
    )
    trades = data.get("trades", [])
    if not trades:
        return {
            "total_trades": 0, "winning": 0, "losing": 0, "win_rate": 0.0,
            "total_pnl": 0.0, "avg_pnl": 0.0, "avg_rr": 0.0,
            "best_trade": 0.0, "worst_trade": 0.0, "avg_duration_s": 0.0,
        }

    pnls = [float(t.get("pnl_usd", 0)) for t in trades]
    winning = [p for p in pnls if p > 0]
    losing = [p for p in pnls if p <= 0]
    total = len(pnls)
    return {
        "total_trades": total,
        "winning": len(winning),
        "losing": len(losing),
        "win_rate": round(len(winning) * 100.0 / total, 2) if total else 0.0,
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl": round(sum(pnls) / total, 2) if total else 0.0,
        "avg_rr": round(sum(float(t.get("rr_ratio", 0)) for t in trades) / total, 2) if total else 0.0,
        "best_trade": round(max(pnls), 2) if pnls else 0.0,
        "worst_trade": round(min(pnls), 2) if pnls else 0.0,
        "avg_duration_s": round(
            sum(float(t.get("duration_s", 0)) for t in trades) / total, 1
        ) if total else 0.0,
    }


@router.get("/{trade_id}")
async def get_trade(trade_id: str) -> dict[str, Any]:
    data = await _proxy_trades({"page": 1, "page_size": 200})
    for t in data.get("trades", []):
        if str(t.get("id")) == trade_id:
            return t
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Trade not found")
