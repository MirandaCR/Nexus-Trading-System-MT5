"""
Market router — proxy live candles/ticks from nexus-data service.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

from ..http_client import new_client
from fastapi import APIRouter, HTTPException, Query

DATA_SERVICE_URL = os.getenv("NEXUS_DATA_URL", "http://localhost:9001").rstrip("/")
STREAM_SYMBOLS = [
    s.strip().upper()
    for s in os.getenv("STREAM_SYMBOLS", "XAUUSD").split(",")
    if s.strip()
]

router = APIRouter(prefix="/api/v2/market", tags=["market"])


def _response(data: Any) -> dict[str, Any]:
    return {
        "success": True,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/symbols")
async def symbols() -> dict[str, Any]:
    return _response(STREAM_SYMBOLS)


@router.get("/candles")
async def candles(
    symbol: str = Query(..., min_length=3),
    timeframe: str = Query("M15"),
    count: int = Query(500, ge=1, le=5000),
) -> dict[str, Any]:
    symbol = symbol.upper().replace("/", "")
    url = f"{DATA_SERVICE_URL}/api/data/ohlcv/{symbol}/{timeframe}"
    try:
        async with new_client(timeout=20.0) as client:
            res = await client.get(url, params={"limit": count})
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"data service unreachable: {exc}",
        ) from exc

    if res.status_code == 404:
        return _response([])
    try:
        res.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"data service error: {exc}",
        ) from exc
    return _response(res.json())


@router.get("/ticks")
async def ticks(
    symbol: str = Query(..., min_length=3),
    count: int = Query(50, ge=1, le=1000),
    source: str = Query("cache", pattern="^(cache|db)$"),
) -> dict[str, Any]:
    symbol = symbol.upper().replace("/", "")
    url = f"{DATA_SERVICE_URL}/api/data/ticks/{symbol}"
    try:
        async with new_client(timeout=5.0) as client:
            res = await client.get(url, params={"count": count, "source": source})
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"data service unreachable: {exc}",
        ) from exc

    if res.status_code == 404:
        return _response([])
    try:
        res.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"data service error: {exc}",
        ) from exc
    return _response(res.json())
