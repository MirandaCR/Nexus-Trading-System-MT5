"""
Agents router — agent status, reasoning history, and manual evaluation trigger.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from ..http_client import new_client
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from shared.constants import (
    AGENT_COLORS,
    AGENT_NAMES,
    AgentID,
    REDIS_KEY_PREFIX,
    SERVICE_PORTS,
)
from shared.duckdb_client import DuckDBClient
from shared.redis_client import RedisClient

from ..deps import get_duckdb, get_redis

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])

AGENTS_BASE = os.getenv(
    "NEXUS_AGENTS_URL",
    f"http://127.0.0.1:{SERVICE_PORTS['nexus-agents']}",
).rstrip("/")

FRESH_WINDOW_SEC = 300  # align with heartbeat TTL


# ── Response models ──────────────────────────────────────────────────────────

class AgentStatus(BaseModel):
    agent_id: str
    name: str
    color: str
    status: str = "idle"
    last_message: str = ""
    last_active: str | None = None


class AgentsMeta(BaseModel):
    service_online: bool = False
    active_cycles: int = 0
    total_cycles_completed: int = 0
    ready_count: int = 0
    total_agents: int = 13
    pipeline_state: str = "offline"  # offline | ready | evaluating | error


class AgentsStatusPayload(BaseModel):
    agents: list[AgentStatus]
    meta: AgentsMeta


class EvaluateRequest(BaseModel):
    symbol: str = "EURUSD"
    timeframe: str = "M5"


def _parse_health_age(ts: str | None, now: datetime) -> bool:
    if not ts:
        return False
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age = (now - parsed).total_seconds()
        return 0 <= age <= FRESH_WINDOW_SEC
    except Exception:
        return False


async def _fetch_agents_service_meta() -> tuple[bool, int, int]:
    """Quick ping to nexus-agents for cycle counters."""
    try:
        async with new_client(timeout=2.5) as client:
            health = await client.get(f"{AGENTS_BASE}/health")
            if health.status_code != 200:
                return False, 0, 0
            status_resp = await client.get(f"{AGENTS_BASE}/api/agents/status")
            if status_resp.status_code != 200:
                return True, 0, 0
            body = status_resp.json()
            return (
                True,
                int(body.get("active_cycles", 0)),
                int(body.get("total_cycles_completed", 0)),
            )
    except Exception:
        return False, 0, 0


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/status", response_model=AgentsStatusPayload)
async def agents_status(
    redis: RedisClient = Depends(get_redis),
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    agent_ids = list(AgentID)

    raw_values: list[Any] = [None] * len(agent_ids)
    try:
        keys = [f"{REDIS_KEY_PREFIX}health:{aid}" for aid in agent_ids]
        raw_values = await redis.r.mget(keys)
    except Exception:
        logger.warning("agents.status.mget_failed")

    results: list[dict[str, Any]] = []
    for aid, raw in zip(agent_ids, raw_values):
        cached: Any = None
        if raw is not None:
            try:
                cached = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            except Exception:
                cached = None

        status_val = "offline"
        last_active: str | None = None
        if isinstance(cached, dict):
            last_active = cached.get("ts")
            reported = str(cached.get("status") or "").lower()
            if _parse_health_age(last_active, now) and reported:
                status_val = reported if reported in (
                    "online", "processing", "idle", "error",
                ) else "online"

        results.append({
            "agent_id": aid,
            "name": AGENT_NAMES.get(aid, aid),
            "color": AGENT_COLORS.get(aid, "#FFFFFF"),
            "status": status_val,
            "last_message": "",
            "last_active": last_active,
        })

    service_online, active_cycles, total_cycles = await _fetch_agents_service_meta()

    # Service alive but Redis keys missing/stale → show READY (idle), not offline
    if service_online:
        for row in results:
            if row["status"] == "offline":
                row["status"] = "idle"
                row["last_message"] = "awaiting signal"

    ready_count = sum(
        1 for a in results
        if a["status"] in ("idle", "online", "processing")
    )

    if not service_online:
        pipeline_state = "offline"
    elif active_cycles > 0:
        pipeline_state = "evaluating"
    elif any(a["status"] == "error" for a in results):
        pipeline_state = "error"
    else:
        pipeline_state = "ready"

    return {
        "agents": results,
        "meta": {
            "service_online": service_online,
            "active_cycles": active_cycles,
            "total_cycles_completed": total_cycles,
            "ready_count": ready_count,
            "total_agents": len(results),
            "pipeline_state": pipeline_state,
        },
    }


@router.get("/reasoning/{cycle_id}")
async def agent_reasoning(
    cycle_id: str,
    db: DuckDBClient = Depends(get_duckdb),
) -> dict[str, Any]:
    """Return every agent log entry that belongs to *cycle_id*."""
    try:
        rows = await db.query(
            """
            SELECT *
            FROM agent_logs
            WHERE metadata::JSON->>'cycle_id' = ?
               OR metadata LIKE ?
            ORDER BY ts
            """,
            [cycle_id, f"%{cycle_id}%"],
        )
    except Exception:
        rows = []
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cycle not found")
    return {"cycle_id": cycle_id, "messages": rows, "count": len(rows)}


@router.post("/evaluate", status_code=status.HTTP_202_ACCEPTED)
async def trigger_evaluation(body: EvaluateRequest) -> dict[str, str]:
    """Proxy a manual evaluation request to the agents service."""
    async with new_client(timeout=15) as client:
        try:
            resp = await client.post(
                f"{AGENTS_BASE}/api/agents/evaluate/{body.symbol}",
                json=body.model_dump(),
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(exc.response.status_code, exc.response.text)
        except httpx.RequestError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Agents service unreachable: {exc}",
            )
    return {"status": "evaluation_triggered", "symbol": body.symbol}


@router.get("/decision/{symbol}")
async def latest_decision(symbol: str) -> dict[str, Any]:
    """Forward the most recent verdict + risk reasoning for a symbol."""
    async with new_client(timeout=10) as client:
        try:
            resp = await client.get(f"{AGENTS_BASE}/api/agents/decision/{symbol.upper()}")
        except httpx.RequestError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Agents service unreachable: {exc}",
            )
    if resp.status_code == 404:
        raise HTTPException(404, "No decision yet for this symbol")
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text)
    return resp.json()

@router.get("/brain/budget")
async def brain_budget() -> dict[str, Any]:
    """Daily LLM cost tracker — remaining budget, token usage."""
    async with new_client(timeout=10) as client:
        try:
            resp = await client.get(f"{AGENTS_BASE}/api/agents/brain/budget")
        except httpx.RequestError:
            return {"error": "agents service unreachable", "allowed": True, "cost_usd": 0.0}
    if resp.status_code >= 400:
        return {"error": resp.text, "allowed": True, "cost_usd": 0.0}
    return resp.json()

@router.get("/shadow-trades/stats")
async def shadow_trade_stats(limit: int = 500) -> dict[str, Any]:
    """What rejected/downgraded signals would have done — validates the
    conflict-lock, ergodic circuit breaker and institutional thresholds
    against real outcomes instead of guessing."""
    async with new_client(timeout=10) as client:
        try:
            resp = await client.get(
                f"{AGENTS_BASE}/api/agents/shadow-trades/stats",
                params={"limit": limit},
            )
        except httpx.RequestError:
            return {"error": "agents service unreachable", "total": 0}
    if resp.status_code >= 400:
        return {"error": resp.text, "total": 0}
    return resp.json()
