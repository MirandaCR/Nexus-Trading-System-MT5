"""
Positions router — open-position CRUD and close/modify proxying.

Falls back to the executor's live broker positions when Redis/DuckDB are empty
(local dev / MT5 terminal).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

from ..http_client import new_client
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from shared.constants import SERVICE_PORTS, Verdict
from shared.duckdb_client import DuckDBClient
from shared.models import Position
from shared.redis_client import RedisClient

from ..deps import get_duckdb, get_redis

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/positions", tags=["positions"])

EXECUTOR_URL = os.getenv(
    "NEXUS_EXECUTOR_URL",
    f"http://127.0.0.1:{SERVICE_PORTS['nexus-executor']}",
).rstrip("/")


# ── Response / request models ────────────────────────────────────────────────

class ModifyRequest(BaseModel):
    sl: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None


class PositionList(BaseModel):
    positions: list[Position]
    count: int
    nexus_count: int = 0
    external_count: int = 0
    account: dict[str, Any] | None = None
    live_connected: bool = False
    source: str = "cache"


def _parse_opened_at(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _executor_row_to_position(p: dict[str, Any]) -> Position:
    side_raw = str(p.get("side", "BUY")).upper()
    side = Verdict.BUY if side_raw == "BUY" else Verdict.SELL
    opened_at = _parse_opened_at(p.get("opened_at"))
    duration_s = max(0.0, (datetime.now(timezone.utc) - opened_at).total_seconds())
    tp = float(p.get("tp") or 0)
    return Position(
        id=str(p.get("id", "")),
        symbol=str(p.get("symbol", "")),
        side=side,
        entry_price=float(p.get("entry_price", 0)),
        current_price=float(p.get("current_price", 0)),
        sl=float(p.get("sl") or 0),
        tp1=tp,
        tp2=None,
        tp3=None,
        size=float(p.get("size", 0.01)),
        pnl_pips=float(p.get("pnl_pips", 0)),
        pnl_usd=float(p.get("pnl_usd", 0)),
        duration=duration_s,
        status="open",
        opened_at=opened_at,
    )


def _dict_to_position(row: dict[str, Any]) -> Position | None:
    try:
        return Position.model_validate(row)
    except Exception:
        return None


async def _fetch_live_executor_snapshot() -> tuple[
    list[Position], dict[str, Any] | None, bool, int, int,
]:
    url = f"{EXECUTOR_URL}/api/executor/positions"
    async with new_client(timeout=25.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    rows = data.get("positions") or []
    account = data.get("account")
    nexus_count = int(data.get("nexus_count", 0))
    external_count = int(data.get("external_count", 0))
    return (
        [_executor_row_to_position(dict(r)) for r in rows],
        account if isinstance(account, dict) else None,
        True,
        nexus_count,
        external_count,
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("", response_model=PositionList)
async def list_positions(
    symbol: str | None = Query(None),
    status_filter: str = Query("open", alias="status"),
    redis: RedisClient = Depends(get_redis),
    db: DuckDBClient = Depends(get_duckdb),
) -> dict[str, Any]:
    """Return open positions from Redis cache, DuckDB, or live executor (MT5)."""
    source = "cache"
    cached = await redis.cache_get("positions:all")
    if cached and isinstance(cached, list):
        positions_raw: list[Any] = cached
    else:
        source = "duckdb"
        try:
            rows = await db.query(
                "SELECT * FROM positions WHERE status = ? ORDER BY opened_at DESC",
                [status_filter],
            )
            positions_raw = rows
            await redis.cache_set("positions:all", positions_raw, ttl_sec=5)
        except Exception as exc:
            positions_raw = []
            await logger.awarning("positions.duckdb_unavailable", error=str(exc))

    if symbol:
        positions_raw = [
            p for p in positions_raw
            if isinstance(p, dict) and p.get("symbol") == symbol
        ]

    normalized: list[Position] = []
    for p in positions_raw:
        if isinstance(p, Position):
            normalized.append(p)
        elif isinstance(p, dict):
            pos = _dict_to_position(p)
            if pos is not None:
                normalized.append(pos)

    account: dict[str, Any] | None = None
    live_connected = False
    nexus_count = 0
    external_count = 0

    try:
        (
            exec_positions,
            exec_account,
            exec_connected,
            nexus_count,
            external_count,
        ) = await _fetch_live_executor_snapshot()
        account = exec_account
        live_connected = exec_connected
        if not normalized:
            normalized = exec_positions
            source = "executor"
        if symbol:
            normalized = [p for p in normalized if p.symbol == symbol]
    except Exception as exc:
        await logger.awarning("positions.executor_snapshot_failed", error=str(exc))

    if not normalized:
        source = "empty"

    return {
        "positions": normalized,
        "count": len(normalized),
        "nexus_count": nexus_count,
        "external_count": external_count,
        "account": account,
        "live_connected": live_connected,
        "source": source,
    }


@router.get("/{position_id}", response_model=Position)
async def get_position(
    position_id: str,
    redis: RedisClient = Depends(get_redis),
    db: DuckDBClient = Depends(get_duckdb),
) -> Any:
    cached = await redis.cache_get(f"positions:{position_id}")
    if cached:
        return cached

    rows = await db.query(
        "SELECT * FROM positions WHERE id = ?", [position_id],
    )
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Position not found")
    return rows[0]


@router.post("/{position_id}/close", status_code=status.HTTP_202_ACCEPTED)
async def close_position(position_id: str) -> dict[str, str]:
    """Proxy close to the executor (DELETE …/api/executor/position/{ticket})."""
    async with new_client(timeout=45.0) as client:
        try:
            resp = await client.delete(
                f"{EXECUTOR_URL}/api/executor/position/{position_id}",
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(exc.response.status_code, exc.response.text)
        except httpx.RequestError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Executor unreachable: {exc}",
            )
    return {"status": "close_requested", "position_id": position_id}


@router.post("/{position_id}/modify", status_code=status.HTTP_202_ACCEPTED)
async def modify_position(
    position_id: str,
    body: ModifyRequest,
) -> dict[str, Any]:
    """Proxy modify SL/TP to the executor."""
    exec_body: dict[str, float] = {}
    if body.sl is not None:
        exec_body["sl"] = body.sl
    tp_val = body.tp1 if body.tp1 is not None else body.tp2 if body.tp2 is not None else body.tp3
    if tp_val is not None:
        exec_body["tp"] = tp_val

    if not exec_body:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Provide at least one of sl, tp1, tp2, tp3",
        )

    async with new_client(timeout=45.0) as client:
        try:
            resp = await client.put(
                f"{EXECUTOR_URL}/api/executor/position/{position_id}",
                json=exec_body,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(exc.response.status_code, exc.response.text)
        except httpx.RequestError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Executor unreachable: {exc}",
            )
    return {"status": "modify_requested", "position_id": position_id, **exec_body}
