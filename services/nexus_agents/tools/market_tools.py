"""
Market data tools used by agents to query prices, indicators,
and OHLCV data from DuckDB and internal services.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from shared.http_client import new_client
import structlog

logger = structlog.get_logger(__name__)

DATA_SERVICE_URL = os.getenv("NEXUS_DATA_URL", "http://localhost:8001")
FEATURES_SERVICE_URL = os.getenv("NEXUS_FEATURES_URL", "http://localhost:8002")

# Broker symbol aliases for intermarket instruments (try in order)
_SYMBOL_ALIASES: dict[str, list[str]] = {
    "DXY": ["DXY", "USDX", "USDIDX"],
    "US10Y": ["US10Y", "UST10Y", "TNX"],
    "VIX": ["VIX", "VIXUSD", "USVIX"],
    "USOIL": ["USOIL", "WTI", "XTIUSD", "CL"],
    "US500": ["US500", "SPX500", "SP500", "US500Cash"],
}


async def query_duckdb(sql: str) -> list[dict[str, Any]]:
    """
    Fetch OHLCV data from Redis cache (written by nexus-data).
    The SQL parameter is parsed to extract symbol and timeframe.
    """
    symbol = ""
    timeframe = ""
    sql_upper = sql.upper()
    if "SYMBOL=" in sql_upper:
        try:
            idx = sql.index("symbol=")
            rest = sql[idx + 8:]
            symbol = rest.split("'")[0]
        except (ValueError, IndexError):
            pass
    if "TIMEFRAME=" in sql_upper:
        try:
            idx = sql.index("timeframe=")
            rest = sql[idx + 11:]
            timeframe = rest.split("'")[0]
        except (ValueError, IndexError):
            pass

    if not symbol or not timeframe:
        return []

    try:
        import redis.asyncio as aioredis
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        r = aioredis.from_url(redis_url, decode_responses=True)
        raw = await r.get(f"nexus:ohlcv:{symbol}:{timeframe}")
        await r.aclose()
        if raw:
            return json.loads(raw)
    except Exception as exc:
        await logger.awarning("tools.query_ohlcv_failed", error=str(exc))

    return []


async def get_price(symbol: str) -> dict[str, float]:
    """Get current bid/ask for a symbol from the data service."""
    try:
        async with new_client(timeout=3.0) as client:
            resp = await client.get(
                f"{DATA_SERVICE_URL}/api/data/ticks/{symbol}",
                params={"count": 1, "source": "cache"},
            )
            if resp.status_code == 200:
                ticks = resp.json()
                if ticks and isinstance(ticks, list) and len(ticks) > 0:
                    t = ticks[0]
                    return {
                        "bid": t.get("bid", 0.0),
                        "ask": t.get("ask", 0.0),
                        "spread": t.get("spread", 0.0),
                    }
    except Exception:
        pass

    return {"bid": 0.0, "ask": 0.0, "spread": 0.0}


async def _fetch_indicators_for_symbol(symbol: str, timeframe: str) -> dict[str, Any]:
    """Fetch indicators for one symbol (HTTP then Redis)."""
    try:
        async with new_client(timeout=5.0) as client:
            resp = await client.get(
                f"{FEATURES_SERVICE_URL}/api/features/{symbol}/{timeframe}"
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and "features" in data:
                    return data["features"]
                return data
    except Exception:
        pass

    try:
        import redis.asyncio as aioredis
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        r = aioredis.from_url(redis_url, decode_responses=True)
        raw = await r.get(f"features:{symbol}:{timeframe}")
        await r.aclose()
        if raw:
            cached = json.loads(raw)
            if isinstance(cached, dict) and "features" in cached:
                return cached["features"]
            return cached
    except Exception:
        pass

    return {}


async def get_indicators(symbol: str, timeframe: str) -> dict[str, Any]:
    """Fetch the latest indicator values for a symbol/timeframe pair."""
    sym = symbol.upper().replace("/", "")
    candidates = _SYMBOL_ALIASES.get(sym, [sym])
    for candidate in candidates:
        result = await _fetch_indicators_for_symbol(candidate, timeframe)
        if result:
            return result
    return {}


async def calculate_sr_levels(symbol: str, timeframe: str) -> dict[str, Any]:
    """Calculate support and resistance levels from OHLCV data."""
    rows: list[dict] = []
    try:
        import redis.asyncio as aioredis
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        r = aioredis.from_url(redis_url, decode_responses=True)
        raw = await r.get(f"nexus:ohlcv:{symbol}:{timeframe}")
        await r.aclose()
        if raw:
            rows = json.loads(raw)
    except Exception:
        pass

    if not rows:
        return {"support": [], "resistance": []}

    highs = sorted([r["high"] for r in rows if r.get("high")], reverse=True)
    lows = sorted([r["low"] for r in rows if r.get("low")])

    def _cluster(values: list[float], threshold: float = 0.0005) -> list[float]:
        """Group nearby price levels into clusters."""
        if not values:
            return []
        clusters: list[list[float]] = [[values[0]]]
        for v in values[1:]:
            if abs(v - clusters[-1][-1]) / max(clusters[-1][-1], 1e-10) < threshold:
                clusters[-1].append(v)
            else:
                clusters.append([v])
        return [sum(c) / len(c) for c in clusters if len(c) >= 2][:5]

    return {
        "support": _cluster(lows),
        "resistance": _cluster(highs),
    }


# ── Higher-timeframe mapping by operational timeframe ────────────────────────
# For each operational timeframe, define which HTFs provide meaningful context.
_HTF_MAP: dict[str, list[str]] = {
    "M1":  ["M5", "M15", "H1"],
    "M5":  ["M15", "H1", "H4"],
    "M15": ["H1", "H4", "D1"],
    "M30": ["H1", "H4", "D1"],
    "H1":  ["H4", "D1"],
    "H4":  ["D1"],
}


async def get_htf_context(symbol: str, timeframe: str = "M15") -> dict[str, Any]:
    """
    Fetch higher-timeframe indicators relative to the operational timeframe
    and return a bias summary with alignment score.

    Returns::

        {
            "bias": "BULLISH" | "BEARISH" | "NEUTRAL",
            "alignment_score": int,      # -4 to +4
            "htf_details": {timeframe: {rsi, ema_20, ema_50, ema_200, macd_histogram, close}, ...},
            "operational_tf": str,
            "htf_list": [str, ...],
        }
    """
    htf_list = _HTF_MAP.get(timeframe, ["H1", "H4", "D1"])

    htf_details: dict[str, dict] = {}
    for htf in htf_list:
        try:
            ind = await get_indicators(symbol, htf)
            if ind:
                htf_details[htf] = {
                    "rsi": ind.get("rsi"),
                    "ema_9": ind.get("ema_9"),
                    "ema_20": ind.get("ema_20"),
                    "ema_21": ind.get("ema_21"),
                    "ema_50": ind.get("ema_50"),
                    "ema_200": ind.get("ema_200"),
                    "macd_histogram": ind.get("macd_histogram"),
                    "close": ind.get("close"),
                    "atr": ind.get("atr_14", ind.get("atr")),
                    "trend_direction": ind.get("trend_direction"),
                    "trend_score": ind.get("trend_score"),
                }
        except Exception:
            pass

    # Calculate alignment score (-4 to +4)
    score = 0
    for tf, feats in htf_details.items():
        ema9 = feats.get("ema_9") or feats.get("ema_20")
        ema21 = feats.get("ema_21") or feats.get("ema_50")
        if ema9 is not None and ema21 is not None:
            score += 1 if ema9 > ema21 else -1

        rsi = feats.get("rsi")
        if rsi is not None:
            score += 1 if rsi > 50 else -1

        hist = feats.get("macd_histogram")
        if hist is not None:
            score += 1 if hist > 0 else -1

        # EMA 200 check only for H1+
        if tf in ("H1", "H4", "D1"):
            ema200 = feats.get("ema_200")
            close_ = feats.get("close")
            if ema200 is not None and close_ is not None:
                score += 1 if close_ > ema200 else -1

    score = max(-4, min(4, score))

    if score >= 2:
        bias = "BULLISH"
    elif score <= -2:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    return {
        "bias": bias,
        "alignment_score": score,
        "htf_details": htf_details,
        "operational_tf": timeframe,
        "htf_list": htf_list,
    }
