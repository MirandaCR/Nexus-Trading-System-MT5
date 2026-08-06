"""
Regime Router — exposes current HMM + rule-based regime data to the dashboard.
"""
from __future__ import annotations

import json
import logging
import os

import redis.asyncio as aioredis
from fastapi import APIRouter

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

_redis: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True, max_connections=5)
    return _redis


router = APIRouter(prefix="/api/regime", tags=["Regime"])


@router.get("/current")
async def get_current_regime():
    """Current market regime from HMM + rule-based classifier."""
    try:
        r = await _get_redis()
        raw = await r.get("nexus:regime:current")
        if raw:
            return json.loads(raw)
        return {
            "regime": "UNKNOWN",
            "hmm_regime": "UNKNOWN",
            "rule_regime": "UNKNOWN",
            "hmm_confidence": 0.0,
            "source": "none",
            "is_transition": False,
            "volatility_regime": "MEDIUM",
        }
    except Exception:
        logger.debug("regime/current error", exc_info=True)
        return {"regime": "UNKNOWN", "error": "redis unavailable"}


@router.get("/history")
async def get_regime_history(limit: int = 20):
    """Recent regime transitions (last N)."""
    try:
        r = await _get_redis()
        raw_list = await r.lrange("nexus:regime:history", 0, limit - 1)
        return {"transitions": [json.loads(x) for x in raw_list]}
    except Exception:
        return {"transitions": []}


@router.get("/thresholds")
async def get_dynamic_thresholds():
    """Current dynamic thresholds derived from TradeMemory."""
    try:
        r = await _get_redis()
        raw = await r.get("nexus:trade_memory:thresholds")
        if raw:
            return json.loads(raw)
        return {"error": "no thresholds computed yet", "total_trades": 0}
    except Exception:
        return {"error": "redis unavailable"}
