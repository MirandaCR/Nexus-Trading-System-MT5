"""
System router — health dashboard, kill-switch, auto-trade toggle, mode switch.
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from shared.constants import ServiceName, TradingMode
from shared.models import SystemHealth
from shared.redis_client import RedisClient

from ..deps import get_config, get_redis
from ..middleware.auth import verify_pin

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])


# ── Request / response models ────────────────────────────────────────────────

class KillSwitchRequest(BaseModel):
    pin: str
    reason: str = "manual kill-switch"


class ToggleAutoTradeRequest(BaseModel):
    enabled: bool


class SwitchModeRequest(BaseModel):
    mode: TradingMode
    pin: str | None = None


class ModeResponse(BaseModel):
    mode: TradingMode
    auto_trade: bool


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/health", response_model=list[SystemHealth])
async def system_health(
    redis: RedisClient = Depends(get_redis),
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for svc in ServiceName:
        cached = await redis.cache_get(f"health:{svc}")
        if cached and isinstance(cached, dict):
            results.append(cached)
        else:
            results.append(
                SystemHealth(service_name=svc, status="unknown").model_dump(mode="json"),
            )
    return results


@router.post("/kill-switch", status_code=status.HTTP_200_OK)
async def kill_switch(
    body: KillSwitchRequest,
    redis: RedisClient = Depends(get_redis),
) -> dict[str, str]:
    """EMERGENCY STOP — close all positions, halt all agents."""
    verify_pin(body.pin)
    await redis.cache_set("system:kill_switch", True, ttl_sec=86400)
    await redis.cache_set("system:auto_trade", False, ttl_sec=86400)
    await redis.publish_channel(
        "system:commands",
        {"command": "kill_switch", "reason": body.reason},
    )
    await logger.awarning("KILL_SWITCH activated", reason=body.reason)
    return {"status": "kill_switch_activated", "reason": body.reason}


@router.post("/toggle-auto-trade")
async def toggle_auto_trade(
    body: ToggleAutoTradeRequest,
    redis: RedisClient = Depends(get_redis),
) -> dict[str, Any]:
    await redis.cache_set("system:auto_trade", body.enabled, ttl_sec=86400)
    state = "enabled" if body.enabled else "disabled"
    await logger.ainfo("auto_trade.toggled", enabled=body.enabled)
    return {"auto_trade": body.enabled, "message": f"Auto-trade {state}"}


@router.get("/mode", response_model=ModeResponse)
async def get_mode(
    redis: RedisClient = Depends(get_redis),
) -> dict[str, Any]:
    mode = TradingMode.PAPER
    try:
        session = await redis.cache_get("session:state")
        if session and isinstance(session, dict):
            raw_mode = session.get("mode", "paper")
            try:
                mode = TradingMode(raw_mode) if isinstance(raw_mode, str) else TradingMode.PAPER
            except ValueError:
                mode = TradingMode.PAPER
    except Exception:
        pass

    auto_trade = await redis.cache_get("system:auto_trade")
    return {"mode": mode, "auto_trade": bool(auto_trade) if auto_trade is not None else True}


@router.post("/mode", response_model=ModeResponse)
async def switch_mode(
    body: SwitchModeRequest,
    redis: RedisClient = Depends(get_redis),
) -> dict[str, Any]:
    if body.mode == TradingMode.LIVE:
        if not body.pin:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "PIN required to switch to live mode",
            )
        verify_pin(body.pin)

    session = await redis.cache_get("session:state") or {}
    if isinstance(session, dict):
        session["mode"] = body.mode
    else:
        session = {"mode": body.mode}
    await redis.cache_set("session:state", session, ttl_sec=86400)

    await redis.publish_channel(
        "system:commands",
        {"command": "mode_switch", "mode": body.mode},
    )
    await logger.ainfo("mode.switched", mode=body.mode)

    auto_trade = await redis.cache_get("system:auto_trade")
    return {"mode": body.mode, "auto_trade": bool(auto_trade) if auto_trade is not None else False}
