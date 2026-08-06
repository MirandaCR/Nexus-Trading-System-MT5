"""
News router — latest news feed, calendar events, and per-symbol sentiment.
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query

from shared.models import CalendarEvent, NewsItem
from shared.redis_client import RedisClient

from ..deps import get_redis

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/news", tags=["news"])


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("", response_model=list[NewsItem])
async def latest_news(
    limit: int = Query(50, ge=1, le=200),
    impact: str | None = Query(None, description="Filter by impact: low, medium, high"),
    redis: RedisClient = Depends(get_redis),
) -> list[dict[str, Any]]:
    cached = await redis.cache_get("news:latest")
    if cached and isinstance(cached, list):
        items = cached
    else:
        items = []

    if impact:
        items = [n for n in items if n.get("impact") == impact]
    return items[:limit]


@router.get("/calendar", response_model=list[CalendarEvent])
async def economic_calendar(
    currency: str | None = Query(None),
    impact: str | None = Query(None),
    redis: RedisClient = Depends(get_redis),
) -> list[dict[str, Any]]:
    cached = await redis.cache_get("news:calendar")
    if not cached or not isinstance(cached, list):
        return []

    events = cached
    if currency:
        events = [e for e in events if e.get("currency") == currency.upper()]
    if impact:
        events = [e for e in events if e.get("impact") == impact]
    return events


@router.get("/sentiment/{symbol}")
async def symbol_sentiment(
    symbol: str,
    redis: RedisClient = Depends(get_redis),
) -> dict[str, Any]:
    cached = await redis.cache_get(f"news:sentiment:{symbol.upper()}")
    if cached and isinstance(cached, dict):
        return cached
    return {
        "symbol": symbol.upper(),
        "overall_sentiment": "neutral",
        "score": 0.0,
        "headlines_analyzed": 0,
        "sources": [],
    }
