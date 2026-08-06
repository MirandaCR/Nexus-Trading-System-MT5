"""
Intermarket router — correlation matrix, intermarket score, divergences.
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from shared.redis_client import RedisClient

from ..deps import get_redis

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/intermarket", tags=["intermarket"])


# ── Response models ──────────────────────────────────────────────────────────

class CorrelationEntry(BaseModel):
    pair_a: str
    pair_b: str
    correlation: float
    timeframe: str = "H1"


class IntermarketScore(BaseModel):
    symbol: str
    score: float
    components: dict[str, float]
    bias: str


class Divergence(BaseModel):
    symbol: str
    related_asset: str
    divergence_type: str
    severity: str
    detected_at: str


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/matrix", response_model=list[CorrelationEntry])
async def correlation_matrix(
    redis: RedisClient = Depends(get_redis),
) -> list[dict[str, Any]]:
    cached = await redis.cache_get("intermarket:matrix")
    if cached and isinstance(cached, list):
        return cached
    return []


@router.get("/score/{symbol}", response_model=IntermarketScore)
async def intermarket_score(
    symbol: str,
    redis: RedisClient = Depends(get_redis),
) -> dict[str, Any]:
    cached = await redis.cache_get(f"intermarket:score:{symbol.upper()}")
    if cached and isinstance(cached, dict):
        return cached
    return {
        "symbol": symbol.upper(),
        "score": 0.0,
        "components": {},
        "bias": "neutral",
    }


@router.get("/divergences", response_model=list[Divergence])
async def active_divergences(
    redis: RedisClient = Depends(get_redis),
) -> list[dict[str, Any]]:
    cached = await redis.cache_get("intermarket:divergences")
    if cached and isinstance(cached, list):
        return cached
    return []
