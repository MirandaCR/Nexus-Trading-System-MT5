"""
Support / Resistance and key-level detection.

Three independent methods are provided and can be combined:
1. **Clustering** — DBSCAN-style grouping of swing-point prices.
2. **Round numbers** — psychological levels (multiples of 100/50/10 pips).
3. **Order blocks** — the last bullish/bearish candle before a strong move.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

from .trend_analyzer import find_swing_points


@dataclass(frozen=True)
class PriceLevel:
    price: float
    strength: float = 0.0        # 0‒100
    kind: str = "SR"             # SR | SUPPORT | RESISTANCE
    touches: int = 0
    source: str = "clustering"   # clustering | round | order_block


@dataclass(frozen=True)
class OrderBlock:
    high: float
    low: float
    kind: Literal["BULLISH", "BEARISH"]
    index: int
    timestamp: str | None = None


# ── Level clustering ─────────────────────────────────────────────────────────

def cluster_levels(
    levels: list[float],
    tolerance: float | None = None,
) -> list[float]:
    """
    Merge nearby price levels using DBSCAN.

    *tolerance* defaults to 0.1 % of the mean level.
    """
    if not levels:
        return []
    arr = np.array(levels).reshape(-1, 1)
    if tolerance is None:
        tolerance = float(np.mean(arr)) * 0.001
    db = DBSCAN(eps=tolerance, min_samples=1).fit(arr)
    clustered: list[float] = []
    for label in set(db.labels_):
        mask = db.labels_ == label
        clustered.append(float(np.mean(arr[mask])))
    return sorted(clustered)


# ── S/R via swing-point clustering ───────────────────────────────────────────

def detect_sr_levels(
    df: pd.DataFrame,
    method: str = "clustering",
    order: int = 5,
    tolerance: float | None = None,
) -> list[PriceLevel]:
    """
    Detect support/resistance by clustering recent swing-point prices.
    """
    swings = find_swing_points(df, order=order)
    if not swings:
        return []
    prices = [s.price for s in swings]
    merged = cluster_levels(prices, tolerance=tolerance)

    results: list[PriceLevel] = []
    for lvl in merged:
        strength = calculate_level_strength(lvl, df, tolerance=tolerance)
        close = df["close"].iloc[-1]
        kind = "SUPPORT" if lvl < close else "RESISTANCE"
        tol = (tolerance or float(np.mean(prices)) * 0.001)
        touches = sum(1 for p in prices if abs(p - lvl) <= tol)
        results.append(PriceLevel(
            price=round(lvl, 5),
            strength=strength,
            kind=kind,
            touches=touches,
            source=method,
        ))
    return sorted(results, key=lambda l: l.strength, reverse=True)


# ── Level strength scoring ───────────────────────────────────────────────────

def calculate_level_strength(
    level: float,
    df: pd.DataFrame,
    tolerance: float | None = None,
) -> float:
    """
    Score 0‒100 based on:
    * number of touches / rejections near the level
    * volume concentration around that price
    * recency of the last touch
    """
    if tolerance is None:
        tolerance = level * 0.001

    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    n = len(df)

    touches = np.sum((np.abs(high - level) <= tolerance) | (np.abs(low - level) <= tolerance))
    touch_score = min(touches / 6.0, 1.0) * 40

    if "volume" in df.columns:
        vol = df["volume"].values.astype(float)
        near_mask = (np.abs(close - level) <= tolerance)
        if near_mask.any() and vol.sum() > 0:
            vol_ratio = vol[near_mask].sum() / vol.sum()
            vol_score = min(vol_ratio * 10, 1.0) * 30
        else:
            vol_score = 0.0
    else:
        vol_score = 15.0

    last_touch_idx = -1
    for i in range(n - 1, -1, -1):
        if abs(high[i] - level) <= tolerance or abs(low[i] - level) <= tolerance:
            last_touch_idx = i
            break
    if last_touch_idx >= 0:
        recency = (last_touch_idx / max(n - 1, 1))
        recency_score = recency * 30
    else:
        recency_score = 0.0

    return round(min(touch_score + vol_score + recency_score, 100.0), 1)


# ── Round / psychological numbers ────────────────────────────────────────────

def find_round_numbers(
    price: float,
    n_levels: int = 5,
) -> list[PriceLevel]:
    """
    Return the nearest psychological levels above and below *price*.

    Adapts step size to the magnitude of the price:
    * < 10 → step 0.5
    * < 100 → step 5
    * < 1000 → step 50
    * >= 1000 → step 100
    """
    if price < 10:
        step = 0.5
    elif price < 100:
        step = 5.0
    elif price < 1000:
        step = 50.0
    else:
        step = 100.0

    base = round(price / step) * step
    levels: list[PriceLevel] = []
    for i in range(-n_levels, n_levels + 1):
        lvl = round(base + i * step, 5)
        kind = "SUPPORT" if lvl < price else "RESISTANCE" if lvl > price else "SR"
        levels.append(PriceLevel(
            price=lvl,
            strength=50.0,
            kind=kind,
            source="round",
        ))
    return levels


# ── Order-block detection ────────────────────────────────────────────────────

def detect_order_blocks(
    df: pd.DataFrame,
    body_multiplier: float = 1.5,
    lookback: int = 100,
) -> list[OrderBlock]:
    """
    An *order block* is the last opposing candle before a strong impulsive move.

    * Bullish OB: last bearish candle before a strong bullish move.
    * Bearish OB: last bullish candle before a strong bearish move.

    A "strong move" is defined as a body ≥ *body_multiplier* × ATR(14).
    """
    if len(df) < 20:
        return []

    o = df["open"].values.astype(float)
    c = df["close"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    body = np.abs(c - o)

    from shared.pandas_ta_compat import ta as _ta

    atr_s = _ta.atr(df["high"], df["low"], df["close"], length=14)
    if atr_s is None:
        return []
    atr = atr_s.values

    start = max(len(df) - lookback, 14)
    ts_col = df["timestamp"].astype(str).values if "timestamp" in df.columns else None

    blocks: list[OrderBlock] = []
    for i in range(start + 1, len(df)):
        if not np.isfinite(atr[i]):
            continue
        threshold = atr[i] * body_multiplier
        if body[i] < threshold:
            continue

        bullish_move = c[i] > o[i]
        prev_is_opposite = (c[i - 1] < o[i - 1]) if bullish_move else (c[i - 1] > o[i - 1])
        if not prev_is_opposite:
            continue

        blocks.append(OrderBlock(
            high=float(h[i - 1]),
            low=float(l[i - 1]),
            kind="BULLISH" if bullish_move else "BEARISH",
            index=i - 1,
            timestamp=ts_col[i - 1] if ts_col is not None else None,
        ))

    return blocks
