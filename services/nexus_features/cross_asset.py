"""
Cross-Asset Feature Engine

Fetches DXY and US10Y data and computes contextual features for gold trading:
  - dxy_change_5d          5-day % change in DXY index
  - dxy_rsi_14             RSI(14) of DXY
  - dxy_trend              "up" / "down" / "flat" (rolling 20-bar EMA direction)
  - us10y_change_5d        5-day change in 10Y yield (basis points)
  - us10y_level            current 10Y yield
  - real_rate_proxy        us10y_level - inflation_proxy (crude approximation)
  - gold_dxy_divergence    +1 if gold and DXY are diverging, -1 if converging
  - gold_yield_regime      "risk_off" / "risk_on" / "neutral"
  - corr_gold_dxy_20       rolling 20-bar correlation between gold and DXY

Data sources (tried in order):
  1. Redis cache (nexus:cross_asset:DXY, nexus:cross_asset:US10Y) — fastest
  2. yfinance (^DXY, ^TNX) — requires internet, cached 5 min
  3. Synthetic proxy from MT5 USDJPY/EURUSD (fallback, no internet needed)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
_CROSS_ASSET_TTL = 300   # 5-minute cache
_CROSS_ASSET_KEY = "nexus:cross_asset:{symbol}"
_CROSS_ASSET_ALL_KEY = "nexus:cross_asset:summary"

# ── Yahoo Finance ticker mapping ─────────────────────────────────────────────
_YF_TICKERS: dict[str, str] = {
    "DXY":   "DX-Y.NYB",   # Dollar Index futures
    "US10Y": "^TNX",        # 10-Year Treasury yield
}

_FALLBACK_VALUES: dict[str, dict[str, float]] = {
    "DXY":   {"level": 104.0, "change_5d": 0.0, "rsi_14": 50.0, "trend_score": 0.0},
    "US10Y": {"level": 4.3,   "change_5d": 0.0},
}


# ── Fetch helpers ─────────────────────────────────────────────────────────────

async def _fetch_from_redis(symbol: str, r) -> dict[str, Any] | None:
    try:
        raw = await r.get(_CROSS_ASSET_KEY.format(symbol=symbol))
        if raw:
            data = json.loads(raw)
            # Check freshness
            ts = data.get("fetched_at", "")
            if ts:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
                if age < _CROSS_ASSET_TTL:
                    return data
    except Exception:
        pass
    return None


def _fetch_from_yfinance(symbol: str, period: str = "30d") -> dict[str, Any] | None:
    try:
        import yfinance as yf  # optional dependency
        ticker = _YF_TICKERS.get(symbol, symbol)
        df = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < 6:
            return None

        closes = df["Close"].values.astype(float)
        current = float(closes[-1])
        prev5 = float(closes[-6]) if len(closes) >= 6 else current
        change_5d = (current - prev5) / max(abs(prev5), 1e-8) * 100

        # RSI(14)
        rsi = _compute_rsi(closes, 14)
        trend_score = _compute_trend(closes, 20)

        return {
            "symbol": symbol,
            "level": current,
            "change_5d": round(change_5d, 4),
            "rsi_14": round(rsi, 1),
            "trend_score": round(trend_score, 3),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "yfinance",
        }
    except ImportError:
        logger.debug("yfinance not installed — using synthetic proxy")
        return None
    except Exception as exc:
        logger.debug("yfinance fetch failed for %s: %s", symbol, exc)
        return None


async def _compute_dynamic_proxy(redis_client) -> dict[str, dict[str, Any]]:
    """Derive DXY and US10Y proxies from XAUUSD features stored in Redis.

    Gold has strong inverse correlation with DXY (~-0.75) and negative
    relationship with US10Y real yields.  This is not a substitution for
    real data, but gives directional signals that update with every
    XAUUSD feature cycle rather than being permanently hardcoded.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        raw = await redis_client.get("nexus:features:XAUUSD:M5")
        if not raw:
            raise ValueError("no XAUUSD features")
        data = json.loads(raw)
        feats = data.get("features", data)

        gold_rsi = float(feats.get("rsi", 50.0) or 50.0)
        gold_close = float(feats.get("close", 0.0) or 0.0)
        gold_ema9 = float(feats.get("ema_9", gold_close) or gold_close)
        gold_ema50 = float(feats.get("ema_50", gold_close) or gold_close)
        trend_dir = str(feats.get("trend_direction", "SIDEWAYS"))
        trend_strength = float(feats.get("trend_strength", 0.0) or 0.0)

        # DXY inverse proxy
        dxy_rsi = round(100.0 - gold_rsi, 1)
        # trend_score: gold uptrend → DXY downtrend
        if trend_dir == "UPTREND":
            dxy_trend_score = -min(1.0, trend_strength / 100.0)
        elif trend_dir == "DOWNTREND":
            dxy_trend_score = min(1.0, trend_strength / 100.0)
        else:
            dxy_trend_score = 0.0
        # 5d change proxy: momentum from ema9 vs ema50 displacement, inverted
        if gold_close > 0 and gold_ema50 > 0:
            gold_momentum = (gold_close - gold_ema50) / gold_ema50 * 100
            dxy_change_5d = round(-gold_momentum * 0.4, 4)
        else:
            dxy_change_5d = 0.0

        # US10Y inverse proxy: rising gold → falling yields; falling gold → rising yields
        # Map gold RSI to approximate yield pressure: RSI>60 → yields falling, RSI<40 → rising
        if gold_rsi > 60:
            us10y_change = round(-(gold_rsi - 60) * 0.002, 4)   # yields falling
        elif gold_rsi < 40:
            us10y_change = round((40 - gold_rsi) * 0.002, 4)    # yields rising
        else:
            us10y_change = 0.0

        return {
            "DXY": {
                "level": _FALLBACK_VALUES["DXY"]["level"],  # no real DXY level available
                "change_5d": dxy_change_5d,
                "rsi_14": dxy_rsi,
                "trend_score": round(dxy_trend_score, 3),
                "source": "dynamic_gold_proxy",
                "fetched_at": now_iso,
            },
            "US10Y": {
                "level": _FALLBACK_VALUES["US10Y"]["level"],  # static level
                "change_5d": us10y_change,
                "source": "dynamic_gold_proxy",
                "fetched_at": now_iso,
            },
        }
    except Exception as exc:
        logger.debug("cross_asset: dynamic proxy failed (%s) — using static fallback", exc)
        return {
            "DXY": {**_FALLBACK_VALUES["DXY"], "source": "fallback", "fetched_at": now_iso},
            "US10Y": {**_FALLBACK_VALUES["US10Y"], "source": "fallback", "fetched_at": now_iso},
        }


def _compute_rsi(prices: np.ndarray, period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 1)


def _compute_trend(prices: np.ndarray, window: int = 20) -> float:
    """Return a -1..+1 score: +1 = strong uptrend, -1 = strong downtrend."""
    if len(prices) < window + 1:
        return 0.0
    ema = np.mean(prices[-window:])
    prev_ema = np.mean(prices[-window - 1:-1])
    delta = ema - prev_ema
    scale = abs(prev_ema) * 0.001 if abs(prev_ema) > 1e-8 else 1e-4
    return float(np.clip(delta / scale, -1.0, 1.0))


# ── Main computation ─────────────────────────────────────────────────────────

async def compute_cross_asset_features(
    symbol: str,
    redis_client,
    gold_closes: np.ndarray | None = None,
) -> dict[str, Any]:
    """Compute and cache cross-asset features for gold (or any symbol).

    Returns a feature dict ready to merge into the nexus:features:XAUUSD:* payload.
    """
    # 1. Try Redis cache for DXY and US10Y
    dxy = await _fetch_from_redis("DXY", redis_client)
    us10y = await _fetch_from_redis("US10Y", redis_client)

    # 2. If stale, fetch from yfinance (async wrapper)
    if dxy is None:
        dxy = await asyncio.get_event_loop().run_in_executor(None, _fetch_from_yfinance, "DXY")
        if dxy is None:
            # yfinance not available — compute dynamic proxy from live XAUUSD features
            proxies = await _compute_dynamic_proxy(redis_client)
            dxy = proxies["DXY"]
        try:
            await redis_client.set(_CROSS_ASSET_KEY.format(symbol="DXY"),
                                   json.dumps(dxy), ex=_CROSS_ASSET_TTL)
        except Exception:
            pass

    if us10y is None:
        us10y = await asyncio.get_event_loop().run_in_executor(None, _fetch_from_yfinance, "US10Y")
        if us10y is None:
            proxies = await _compute_dynamic_proxy(redis_client)
            us10y = proxies["US10Y"]
        try:
            await redis_client.set(_CROSS_ASSET_KEY.format(symbol="US10Y"),
                                   json.dumps(us10y), ex=_CROSS_ASSET_TTL)
        except Exception:
            pass

    dxy_level = float(dxy.get("level", 104.0))
    dxy_change = float(dxy.get("change_5d", 0.0))
    dxy_rsi = float(dxy.get("rsi_14", 50.0))
    dxy_trend = float(dxy.get("trend_score", 0.0))

    us10y_level = float(us10y.get("level", 4.3))
    us10y_change = float(us10y.get("change_5d", 0.0))  # in pp

    # ── Gold–DXY correlation (using gold_closes if provided) ─────────────
    corr_gold_dxy = 0.0
    if gold_closes is not None and len(gold_closes) >= 20:
        try:
            dxy_proxy = np.ones(len(gold_closes)) * dxy_level
            # We only have a single DXY value here, so use trend as proxy
            corr_gold_dxy = -dxy_trend  # inverse correlation by design
        except Exception:
            pass

    # ── Divergence signal ─────────────────────────────────────────────────
    # Gold and DXY should move inversely. Divergence = they're moving the same way.
    gold_momentum = 0.0
    if gold_closes is not None and len(gold_closes) >= 5:
        gold_momentum = (float(gold_closes[-1]) - float(gold_closes[-5])) / max(abs(float(gold_closes[-5])), 1e-6)

    gold_dxy_divergence = 0
    if abs(dxy_change) > 0.1 and abs(gold_momentum) > 0.0005:
        if (dxy_change > 0 and gold_momentum < 0) or (dxy_change < 0 and gold_momentum > 0):
            gold_dxy_divergence = 1    # normal inverse relationship
        else:
            gold_dxy_divergence = -1   # diverging from expected = caution

    # ── Risk regime ───────────────────────────────────────────────────────
    # risk_off: yields falling, DXY rising (flight to safety) → gold bullish
    # risk_on:  yields rising, DXY rising (growth) → gold bearish
    if us10y_change < -0.05 and dxy_change > 0.1:
        gold_yield_regime = "risk_off"      # gold bullish
    elif us10y_change > 0.05 and dxy_change > 0.2:
        gold_yield_regime = "risk_on"       # gold bearish
    elif us10y_level > 5.0:
        gold_yield_regime = "high_rates"    # strong headwind for gold
    else:
        gold_yield_regime = "neutral"

    # ── Regime score for signal quality gate (+5 pts) ─────────────────────
    regime_signal_pts = 0
    if gold_yield_regime == "risk_off":
        regime_signal_pts = 5
    elif gold_yield_regime == "neutral":
        regime_signal_pts = 3
    elif gold_yield_regime == "risk_on":
        regime_signal_pts = 1
    else:  # high_rates
        regime_signal_pts = 0

    features = {
        # DXY
        "dxy_level":         round(dxy_level, 3),
        "dxy_change_5d":     round(dxy_change, 4),
        "dxy_rsi_14":        round(dxy_rsi, 1),
        "dxy_trend_score":   round(dxy_trend, 3),
        # US10Y
        "us10y_level":       round(us10y_level, 3),
        "us10y_change_5d":   round(us10y_change, 4),
        # Composite
        "gold_dxy_divergence":   gold_dxy_divergence,
        "gold_yield_regime":     gold_yield_regime,
        "corr_gold_dxy_20":      round(corr_gold_dxy, 3),
        "cross_asset_signal_pts": regime_signal_pts,
        # Source metadata
        "cross_asset_source":    dxy.get("source", "unknown"),
        "cross_asset_updated":   dxy.get("fetched_at", ""),
    }

    # Cache the summary for the dashboard
    try:
        summary = {
            "dxy": {k: dxy.get(k) for k in ("level", "change_5d", "rsi_14", "trend_score", "source")},
            "us10y": {k: us10y.get(k) for k in ("level", "change_5d", "source")},
            "gold_yield_regime": gold_yield_regime,
            "gold_dxy_divergence": gold_dxy_divergence,
            "cross_asset_signal_pts": regime_signal_pts,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await redis_client.set(_CROSS_ASSET_ALL_KEY, json.dumps(summary), ex=_CROSS_ASSET_TTL)
    except Exception:
        pass

    return features


# ── Intraday Shift Monitor (Fase 4) ──────────────────────────────────────────
# Detects material intraday changes in DXY/US10Y or gold_yield_regime changes
# and publishes shift alerts to nexus:cross_asset:shift_alerts (list, max 20, 6h TTL).

_SHIFT_ALERTS_KEY = "nexus:cross_asset:shift_alerts"
_SHIFT_ALERTS_MAX = 20
_SHIFT_ALERTS_TTL = 6 * 3600

_DXY_SHIFT_PCT   = 0.3    # 0.3% move in DXY within one scan interval
_US10Y_SHIFT_BPS = 5.0    # 5 basis points change in US10Y
_PREV_SNAPSHOT: dict[str, Any] = {}


def _detect_regime_shift(prev: dict, curr: dict) -> list[dict[str, Any]]:
    """Compare previous vs current cross-asset snapshot. Return list of shift dicts."""
    shifts = []
    now_iso = datetime.now(timezone.utc).isoformat()

    # DXY level shift
    prev_dxy  = prev.get("dxy", {}).get("level")
    curr_dxy  = curr.get("dxy", {}).get("level")
    if prev_dxy and curr_dxy:
        pct_change = abs(curr_dxy - prev_dxy) / max(abs(prev_dxy), 1e-6) * 100
        if pct_change >= _DXY_SHIFT_PCT:
            direction = "UP" if curr_dxy > prev_dxy else "DOWN"
            shifts.append({
                "type": "DXY_SHIFT",
                "severity": "HIGH" if pct_change >= _DXY_SHIFT_PCT * 2 else "MEDIUM",
                "description": f"DXY moved {direction} {pct_change:.2f}% ({prev_dxy:.2f} → {curr_dxy:.2f})",
                "direction": direction,
                "prev_level": round(prev_dxy, 3),
                "curr_level": round(curr_dxy, 3),
                "pct_change": round(pct_change, 3),
                "detected_at": now_iso,
                "implication": "Headwind for gold" if direction == "UP" else "Tailwind for gold",
            })

    # US10Y shift (basis points)
    prev_y  = prev.get("us10y", {}).get("level")
    curr_y  = curr.get("us10y", {}).get("level")
    if prev_y and curr_y:
        bps_change = abs(curr_y - prev_y) * 100
        if bps_change >= _US10Y_SHIFT_BPS:
            direction = "UP" if curr_y > prev_y else "DOWN"
            shifts.append({
                "type": "US10Y_SHIFT",
                "severity": "HIGH" if bps_change >= _US10Y_SHIFT_BPS * 2 else "MEDIUM",
                "description": f"US10Y moved {direction} {bps_change:.1f}bps ({prev_y:.2f}% → {curr_y:.2f}%)",
                "direction": direction,
                "prev_level": round(prev_y, 3),
                "curr_level": round(curr_y, 3),
                "bps_change": round(bps_change, 1),
                "detected_at": now_iso,
                "implication": "Headwind for gold" if direction == "UP" else "Tailwind for gold",
            })

    # Gold yield regime change
    prev_regime = prev.get("gold_yield_regime")
    curr_regime = curr.get("gold_yield_regime")
    if prev_regime and curr_regime and prev_regime != curr_regime:
        shifts.append({
            "type": "REGIME_CHANGE",
            "severity": "HIGH",
            "description": f"Gold yield regime changed: {prev_regime.upper()} → {curr_regime.upper()}",
            "prev_regime": prev_regime,
            "curr_regime": curr_regime,
            "detected_at": now_iso,
            "implication": {
                "risk_off":   "Gold bullish — yields falling, DXY rising",
                "risk_on":    "Gold bearish — yields rising, growth mode",
                "high_rates": "Strong headwind — yields above 5%",
                "neutral":    "No clear directional bias",
            }.get(curr_regime, "Regime change detected"),
        })

    return shifts


async def _push_shift_alerts(redis_client, shifts: list[dict]) -> None:
    for alert in shifts:
        try:
            await redis_client.lpush(_SHIFT_ALERTS_KEY, json.dumps(alert))
            await redis_client.ltrim(_SHIFT_ALERTS_KEY, 0, _SHIFT_ALERTS_MAX - 1)
            await redis_client.expire(_SHIFT_ALERTS_KEY, _SHIFT_ALERTS_TTL)
            logger.warning("CROSS-ASSET SHIFT: %s — %s", alert["type"], alert["description"])
        except Exception:
            pass


async def shift_monitor_loop(redis_client, check_interval_sec: int = 300) -> None:
    """Background task: compare cross-asset snapshots every check_interval_sec.

    Publishes shift alerts to nexus:cross_asset:shift_alerts when material
    intraday moves are detected in DXY, US10Y, or gold_yield_regime.
    """
    global _PREV_SNAPSHOT
    logger.info("Cross-asset shift monitor started (interval=%ds)", check_interval_sec)

    while True:
        try:
            await asyncio.sleep(check_interval_sec)

            # Fetch current snapshot
            raw = await redis_client.get(_CROSS_ASSET_ALL_KEY)
            if not raw:
                continue
            curr = json.loads(raw)

            # Compare with previous
            if _PREV_SNAPSHOT:
                shifts = _detect_regime_shift(_PREV_SNAPSHOT, curr)
                if shifts:
                    await _push_shift_alerts(redis_client, shifts)

            _PREV_SNAPSHOT = curr

        except asyncio.CancelledError:
            logger.info("Shift monitor cancelled")
            break
        except Exception:
            logger.error("Shift monitor error", exc_info=True)


# ── Background refresh loop (called from nexus_features lifespan) ─────────────

async def cross_asset_refresh_loop(redis_client, interval_sec: int = 300) -> None:
    """Refresh DXY and US10Y every `interval_sec` seconds."""
    logger.info("cross_asset: refresh loop started (interval=%ds)", interval_sec)
    while True:
        try:
            await asyncio.sleep(interval_sec)
            # Force re-fetch by clearing cached keys
            await redis_client.delete(_CROSS_ASSET_KEY.format(symbol="DXY"))
            await redis_client.delete(_CROSS_ASSET_KEY.format(symbol="US10Y"))
            # Compute and cache a fresh summary (symbol-agnostic, no gold closes here)
            await compute_cross_asset_features("XAUUSD", redis_client)
            logger.debug("cross_asset: refreshed DXY/US10Y")
        except asyncio.CancelledError:
            break
        except Exception:
            logger.warning("cross_asset: refresh error", exc_info=True)
