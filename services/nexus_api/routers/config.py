"""
Config router — read/write system settings, agent configs, and risk rules.

Only non-hardcoded risk fields may be updated (e.g. ``daily_trade_limit``).
Hardcoded safety limits (``max_daily_loss_pct``, ``max_weekly_drawdown_pct``)
require a code change.
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from shared.config_loader import (
    AgentConfigSchema,
    ConfigLoader,
    RiskRulesSchema,
    SettingsSchema,
)
from shared.redis_client import RedisClient

from ..deps import get_config, get_redis

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])

HARDCODED_RISK_FIELDS = frozenset({
    "max_daily_loss_pct",
    "max_weekly_drawdown_pct",
    "max_open_positions",
})


# ── Request models ───────────────────────────────────────────────────────────

class UpdateSettingsRequest(BaseModel):
    settings: dict[str, Any]


class UpdateAgentConfigRequest(BaseModel):
    enabled: bool | None = None
    temperature: float | None = None
    parameters: dict[str, Any] | None = None


class UpdateRiskRulesRequest(BaseModel):
    rules: dict[str, Any]


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/settings")
async def get_settings(
    cfg: ConfigLoader = Depends(get_config),
) -> dict[str, Any]:
    cached = cfg.get("settings")
    if cached:
        return cached
    return cfg.load_settings()


@router.put("/settings")
async def update_settings(
    body: UpdateSettingsRequest,
    cfg: ConfigLoader = Depends(get_config),
    redis: RedisClient = Depends(get_redis),
) -> dict[str, Any]:
    current = cfg.get("settings") or cfg.load_settings()
    current.update(body.settings)
    try:
        validated = SettingsSchema(**current)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    dumped = validated.model_dump()
    cfg._cache["settings"] = dumped  # noqa: SLF001
    await redis.cache_set("config:settings", dumped, ttl_sec=3600)
    await logger.ainfo("config.settings.updated", changes=list(body.settings.keys()))
    return dumped


@router.get("/agents")
async def get_agent_configs(
    cfg: ConfigLoader = Depends(get_config),
) -> dict[str, Any]:
    cached = cfg.get("agents")
    if cached:
        return cached
    return cfg.load_agents()


@router.put("/agents/{agent_id}")
async def update_agent_config(
    agent_id: str,
    body: UpdateAgentConfigRequest,
    cfg: ConfigLoader = Depends(get_config),
    redis: RedisClient = Depends(get_redis),
) -> dict[str, Any]:
    agents = cfg.get("agents") or cfg.load_agents()
    if agent_id not in agents:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Agent {agent_id} not found")

    current = agents[agent_id]
    updates = body.model_dump(exclude_none=True)
    current.update(updates)

    try:
        validated = AgentConfigSchema(**current)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    agents[agent_id] = validated.model_dump()
    cfg._cache["agents"] = agents  # noqa: SLF001
    await redis.cache_set("config:agents", agents, ttl_sec=3600)
    await logger.ainfo("config.agent.updated", agent_id=agent_id)
    return agents[agent_id]


@router.get("/risk")
async def get_risk_rules(
    cfg: ConfigLoader = Depends(get_config),
) -> dict[str, Any]:
    cached = cfg.get("risk")
    if cached:
        return cached
    try:
        return cfg.load_risk_rules()
    except FileNotFoundError:
        return RiskRulesSchema().model_dump()


@router.put("/risk")
async def update_risk_rules(
    body: UpdateRiskRulesRequest,
    cfg: ConfigLoader = Depends(get_config),
    redis: RedisClient = Depends(get_redis),
) -> dict[str, Any]:
    forbidden = set(body.rules.keys()) & HARDCODED_RISK_FIELDS
    if forbidden:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Cannot modify hardcoded risk fields: {sorted(forbidden)}",
        )

    current = cfg.get("risk")
    if not current:
        try:
            current = cfg.load_risk_rules()
        except FileNotFoundError:
            current = RiskRulesSchema().model_dump()

    current.update(body.rules)
    try:
        validated = RiskRulesSchema(**current)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    dumped = validated.model_dump()
    cfg._cache["risk"] = dumped  # noqa: SLF001
    await redis.cache_set("config:risk", dumped, ttl_sec=3600)
    await logger.ainfo("config.risk.updated", changes=list(body.rules.keys()))
    return dumped
