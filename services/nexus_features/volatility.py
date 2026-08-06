"""
Volatility analysis module.

All functions are vectorised and operate on DataFrames that already
contain OHLCV columns.  The ``detect_regime`` classifier uses ATR
percentile bands to bucket the current environment.
"""
from __future__ import annotations

from enum import StrEnum, unique
from typing import Literal

import numpy as np
import pandas as pd
from shared.pandas_ta_compat import ta


@unique
class VolRegime(StrEnum):
    LOW     = "LOW"
    MEDIUM  = "MEDIUM"
    HIGH    = "HIGH"
    EXTREME = "EXTREME"


# ── ATR normalised by close ──────────────────────────────────────────────────

def calculate_atr_normalized(
    df: pd.DataFrame,
    period: int = 14,
) -> pd.Series:
    atr = ta.atr(df["high"], df["low"], df["close"], length=period)
    return (atr / df["close"]) * 100


# ── Volatility regime detection ──────────────────────────────────────────────

def detect_regime(
    df: pd.DataFrame,
    atr_period: int = 14,
    lookback: int = 100,
) -> pd.Series:
    """
    Classify each bar into a volatility regime using rolling ATR percentile.

    Thresholds (of rolling ATR percentile rank):
        <=25 → LOW, 25-60 → MEDIUM, 60-85 → HIGH, >85 → EXTREME
    """
    atr = ta.atr(df["high"], df["low"], df["close"], length=atr_period)
    pct_rank = atr.rolling(window=lookback, min_periods=atr_period).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False,
    )
    conditions = [
        pct_rank <= 0.25,
        pct_rank <= 0.60,
        pct_rank <= 0.85,
        pct_rank > 0.85,
    ]
    choices = [
        VolRegime.LOW,
        VolRegime.MEDIUM,
        VolRegime.HIGH,
        VolRegime.EXTREME,
    ]
    return pd.Series(
        np.select(conditions, choices, default=VolRegime.MEDIUM),
        index=df.index,
        name="vol_regime",
    )


# ── Bollinger Band width ────────────────────────────────────────────────────

def calculate_bb_width(
    df: pd.DataFrame,
    period: int = 20,
    std: float = 2.0,
) -> pd.Series:
    bb = ta.bbands(df["close"], length=period, std=std)
    if bb is None or bb.empty:
        return pd.Series(np.nan, index=df.index, name="bb_width")
    upper = bb.iloc[:, 2]
    lower = bb.iloc[:, 0]
    mid = bb.iloc[:, 1]
    return ((upper - lower) / mid * 100).rename("bb_width")


# ── Keltner Channel width ───────────────────────────────────────────────────

def calculate_keltner_width(
    df: pd.DataFrame,
    ema_period: int = 20,
    atr_period: int = 14,
    multiplier: float = 1.5,
) -> pd.Series:
    mid = ta.ema(df["close"], length=ema_period)
    atr = ta.atr(df["high"], df["low"], df["close"], length=atr_period)
    upper = mid + multiplier * atr
    lower = mid - multiplier * atr
    return ((upper - lower) / mid * 100).rename("keltner_width")


# ── Historical (realised) volatility ─────────────────────────────────────────

def calculate_historical_vol(
    df: pd.DataFrame,
    window: int = 20,
) -> pd.Series:
    log_ret = np.log(df["close"] / df["close"].shift(1))
    return (log_ret.rolling(window=window).std() * np.sqrt(252)).rename("hist_vol")


# ── Volatility percentile ───────────────────────────────────────────────────

def calculate_vol_percentile(
    df: pd.DataFrame,
    lookback: int = 252,
    window: int = 20,
) -> pd.Series:
    hvol = calculate_historical_vol(df, window=window)
    return hvol.rolling(window=lookback, min_periods=window).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False,
    ).rename("vol_percentile")
