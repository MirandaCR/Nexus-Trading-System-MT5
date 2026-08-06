"""
Brain Budget — daily LLM cost limiter for nexus_agents.

Tracks DeepSeek token usage per day in Redis. When the budget is near
exhaustion, max_tokens is reduced to stretch remaining capacity. When the
budget is fully spent, LLM calls are skipped and a cached/default response
is returned instead of blocking the pipeline.

Redis keys:
    nexus:brain:usage        — JSON {date, input_tokens, output_tokens, cost_usd}

Env vars:
    NEXUS_BRAIN_DAILY_BUDGET_USD   default 2.00
    NEXUS_BRAIN_WARN_PCT           default 0.80 (80% used → throttle tokens)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_REDIS_KEY = "nexus:brain:usage"
_REDIS_TTL = 172_800  # 48h so it survives past midnight

# DeepSeek-V2.5 / deepseek-chat pricing (USD per 1M tokens)
_INPUT_COST_PER_1M = 0.27
_OUTPUT_COST_PER_1M = 1.10


def daily_budget() -> float:
    return float(os.getenv("NEXUS_BRAIN_DAILY_BUDGET_USD", "2.0"))


def warn_pct() -> float:
    return float(os.getenv("NEXUS_BRAIN_WARN_PCT", "0.80"))


def _cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * _INPUT_COST_PER_1M + output_tokens * _OUTPUT_COST_PER_1M) / 1_000_000


async def get_usage(redis_client: Any) -> dict:
    """Return today's usage dict, or an empty record if none exists."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        raw = await redis_client.get(_REDIS_KEY)
        if raw:
            data = json.loads(raw)
            if data.get("date") == today:
                return data
    except Exception:
        pass
    return {"date": today, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


async def record_usage(
    redis_client: Any,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Add token counts for the current call and return the new daily cost total."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        usage = await get_usage(redis_client)
        usage["input_tokens"] = int(usage.get("input_tokens", 0)) + input_tokens
        usage["output_tokens"] = int(usage.get("output_tokens", 0)) + output_tokens
        usage["cost_usd"] = _cost(usage["input_tokens"], usage["output_tokens"])
        usage["date"] = today
        await redis_client.set(_REDIS_KEY, json.dumps(usage), ex=_REDIS_TTL)
        return float(usage["cost_usd"])
    except Exception:
        logger.debug("brain_budget.record_failed", exc_info=True)
        return 0.0


async def budget_status(redis_client: Any) -> dict:
    """Return {allowed, throttled, remaining_usd, pct_used, cost_usd}."""
    limit = daily_budget()
    usage = await get_usage(redis_client)
    spent = float(usage.get("cost_usd", 0.0))
    pct = spent / limit if limit > 0 else 0.0
    return {
        "allowed": pct < 1.0,
        "throttled": pct >= warn_pct() and pct < 1.0,
        "remaining_usd": max(0.0, round(limit - spent, 4)),
        "pct_used": round(pct, 4),
        "cost_usd": round(spent, 4),
        "daily_limit_usd": limit,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }


async def adjusted_max_tokens(redis_client: Any, requested: int) -> int:
    """Return a (possibly reduced) max_tokens that fits within the remaining budget.

    When usage is above the warn threshold, halve max_tokens to stretch capacity.
    """
    status = await budget_status(redis_client)
    if not status["allowed"]:
        return 0  # budget exhausted — caller should skip the LLM call
    if status["throttled"]:
        reduced = max(200, requested // 2)
        logger.info(
            "brain_budget.throttled pct=%.0f%% tokens %d→%d",
            status["pct_used"] * 100, requested, reduced,
        )
        return reduced
    return requested
