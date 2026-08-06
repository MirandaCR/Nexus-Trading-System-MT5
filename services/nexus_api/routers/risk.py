"""
Risk router — current risk state and circuit-breaker status.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from ..http_client import new_client
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from shared.models import CircuitBreaker, RiskState
from shared.redis_client import RedisClient

from ..deps import get_redis

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/risk", tags=["risk"])

RISK_BASE = os.getenv("NEXUS_RISK_URL", f"http://localhost:{os.getenv('NEXUS_RISK_PORT', '8006')}")


async def _risk_proxy(method: str, path: str, body: Any = None) -> Any:
    """Forward a request to the nexus-risk service and return JSON."""
    url = f"{RISK_BASE}{path}"
    async with new_client(timeout=8.0) as client:
        try:
            kwargs: dict[str, Any] = {}
            if body is not None:
                kwargs["json"] = body
            resp = await client.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(exc.response.status_code, exc.response.text)
        except Exception as exc:
            logger.warning("risk.proxy_failed", path=path, error=str(exc))
            raise HTTPException(502, f"nexus-risk unreachable: {exc}")


@router.get("/settings")
async def get_risk_settings():
    """Proxy: current front-configurable runtime settings + live counters."""
    return await _risk_proxy("GET", "/api/risk/settings")


@router.post("/settings")
async def update_risk_settings(req: Request):
    """Proxy: update runtime settings from the dashboard."""
    body = await req.json()
    return await _risk_proxy("POST", "/api/risk/settings", body=body)


@router.post("/reset-day")
async def reset_day():
    """Proxy: reset the daily risk budget so trading resumes."""
    return await _risk_proxy("POST", "/api/risk/reset-day")


@router.post("/pause")
async def pause_trading(paused: bool = True):
    """Proxy: manually pause/resume trading."""
    return await _risk_proxy("POST", f"/api/risk/pause?paused={str(paused).lower()}")


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/status", response_model=RiskState)
async def risk_status(
    redis: RedisClient = Depends(get_redis),
) -> dict[str, Any]:
    """Return the current risk state from Redis."""
    cached = await redis.cache_get("risk:state")
    if cached and isinstance(cached, dict):
        return cached
    return RiskState().model_dump()


@router.get("/circuit-breakers", response_model=list[CircuitBreaker])
async def circuit_breakers(
    redis: RedisClient = Depends(get_redis),
) -> list[dict[str, Any]]:
    cached = await redis.cache_get("risk:state")
    if cached and isinstance(cached, dict):
        return cached.get("circuit_breakers", [])
    return []
