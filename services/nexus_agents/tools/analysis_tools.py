"""
Technical analysis tools for support/resistance, chart pattern
detection, and order flow data retrieval.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from shared.http_client import new_client
import structlog

logger = structlog.get_logger(__name__)

DATA_SERVICE_URL = os.getenv("NEXUS_DATA_URL", "http://localhost:8001")
FEATURES_SERVICE_URL = os.getenv("NEXUS_FEATURES_URL", "http://localhost:8002")


async def calculate_sr(ohlcv_data: list[dict[str, Any]]) -> dict[str, list[float]]:
    """
    Calculate support and resistance levels from OHLCV data.

    Uses pivot-point clustering: groups nearby highs/lows that have been
    tested multiple times into significant S/R zones.
    """
    if not ohlcv_data:
        return {"support": [], "resistance": []}

    highs = [r.get("high", 0) for r in ohlcv_data if r.get("high")]
    lows = [r.get("low", 0) for r in ohlcv_data if r.get("low")]
    closes = [r.get("close", 0) for r in ohlcv_data if r.get("close")]

    if not highs or not lows:
        return {"support": [], "resistance": []}

    current_price = closes[0] if closes else (highs[0] + lows[0]) / 2
    avg_range = sum(h - l for h, l in zip(highs, lows)) / len(highs) if highs else 0.001
    threshold = avg_range * 0.3

    def _find_levels(values: list[float], threshold: float) -> list[float]:
        if not values:
            return []
        sorted_vals = sorted(values)
        clusters: list[list[float]] = [[sorted_vals[0]]]
        for v in sorted_vals[1:]:
            if abs(v - clusters[-1][-1]) <= threshold:
                clusters[-1].append(v)
            else:
                clusters.append([v])
        significant = sorted(clusters, key=len, reverse=True)
        levels = [sum(c) / len(c) for c in significant if len(c) >= 2]
        return [round(l, 5) for l in levels[:5]]

    resistance = [l for l in _find_levels(highs, threshold) if l > current_price]
    support = [l for l in _find_levels(lows, threshold) if l < current_price]

    pivot_points = []
    for i in range(1, len(ohlcv_data) - 1):
        h_prev = ohlcv_data[i - 1].get("high", 0)
        h_curr = ohlcv_data[i].get("high", 0)
        h_next = ohlcv_data[i + 1].get("high", 0)
        if h_curr > h_prev and h_curr > h_next and h_curr > current_price:
            if h_curr not in resistance:
                resistance.append(round(h_curr, 5))

        l_prev = ohlcv_data[i - 1].get("low", 0)
        l_curr = ohlcv_data[i].get("low", 0)
        l_next = ohlcv_data[i + 1].get("low", 0)
        if l_curr < l_prev and l_curr < l_next and l_curr < current_price:
            if l_curr not in support:
                support.append(round(l_curr, 5))

    resistance.sort()
    support.sort(reverse=True)

    return {
        "support": support[:5],
        "resistance": resistance[:5],
    }


async def detect_patterns(ohlcv_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Detect common chart patterns in OHLCV data.

    Detects: engulfing, doji, pin bar, inside bar, double top/bottom (simplified).
    """
    if len(ohlcv_data) < 3:
        return []

    patterns: list[dict[str, Any]] = []

    for i in range(1, min(len(ohlcv_data), 20)):
        curr = ohlcv_data[i]
        prev = ohlcv_data[i - 1] if i > 0 else None

        o, h, l, c = curr.get("open", 0), curr.get("high", 0), curr.get("low", 0), curr.get("close", 0)
        body = abs(c - o)
        full_range = h - l if h > l else 0.0001
        body_ratio = body / full_range

        if body_ratio < 0.1 and full_range > 0:
            patterns.append({
                "pattern": "doji",
                "index": i,
                "significance": "neutral_reversal",
                "description": "Indecision candle — body < 10% of range",
            })

        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        if lower_wick > body * 2 and upper_wick < body * 0.5:
            patterns.append({
                "pattern": "pin_bar_bullish",
                "index": i,
                "significance": "bullish_reversal",
                "description": "Long lower wick suggesting buy rejection",
            })
        elif upper_wick > body * 2 and lower_wick < body * 0.5:
            patterns.append({
                "pattern": "pin_bar_bearish",
                "index": i,
                "significance": "bearish_reversal",
                "description": "Long upper wick suggesting sell rejection",
            })

        if prev:
            po, pc = prev.get("open", 0), prev.get("close", 0)
            prev_body = abs(pc - po)

            if pc < po and c > o and c > po and o < pc and body > prev_body:
                patterns.append({
                    "pattern": "bullish_engulfing",
                    "index": i,
                    "significance": "bullish_reversal",
                    "description": "Bullish engulfing — current candle fully engulfs previous bearish candle",
                })
            elif pc > po and c < o and c < po and o > pc and body > prev_body:
                patterns.append({
                    "pattern": "bearish_engulfing",
                    "index": i,
                    "significance": "bearish_reversal",
                    "description": "Bearish engulfing — current candle fully engulfs previous bullish candle",
                })

            if h <= prev.get("high", 0) and l >= prev.get("low", 0):
                patterns.append({
                    "pattern": "inside_bar",
                    "index": i,
                    "significance": "consolidation",
                    "description": "Inside bar — range contained within previous candle",
                })

    return patterns


async def get_flow(symbol: str) -> dict[str, Any]:
    """
    Get order flow data for a symbol from the data service.

    Returns bid/ask volumes, delta, absorption, spread, imbalance, etc.
    """
    try:
        async with new_client(timeout=5.0) as client:
            resp = await client.get(f"{DATA_SERVICE_URL}/api/data/flow/{symbol}")
            if resp.status_code == 200:
                return resp.json()
    except Exception as exc:
        await logger.awarning("tools.get_flow_failed", error=str(exc))

    return {
        "symbol": symbol,
        "bid_volume": 0,
        "ask_volume": 0,
        "delta": 0,
        "absorption": "none",
        "spread": 0.0,
        "spread_avg": 0.0,
        "imbalance": 0.0,
        "large_orders": [],
        "iceberg_detected": False,
    }
