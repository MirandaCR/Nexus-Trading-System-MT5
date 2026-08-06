"""
Asset Correlation Matrix — rolling 7d / 30d Pearson correlations.

Default assets: XAUUSD (GC=F), BTCUSD (BTC-USD), OIL/WTI (CL=F), SPX (^GSPC).
Configurable via nexus:runtime:settings → corr_assets  (dict {name: ticker}).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger(__name__)

# ── System-wide alert publisher ───────────────────────────────────────────────
_SYS_ALERT_LOG = "nexus:alerts:log"
_SYS_ALERT_PUB = "nexus:alerts:pubsub"
_published_div_keys: set[str] = set()   # avoid re-publishing same divergence


async def _publish_system_alert(r, alert_type: str, message: str, severity: str = "INFO", **kw) -> None:
    payload = json.dumps({
        "type": alert_type, "message": message, "severity": severity,
        "ts": datetime.now(timezone.utc).isoformat(), **kw,
    })
    try:
        await r.lpush(_SYS_ALERT_LOG, payload)
        await r.ltrim(_SYS_ALERT_LOG, 0, 49)
        await r.expire(_SYS_ALERT_LOG, 86400)
        await r.publish(_SYS_ALERT_PUB, payload)
    except Exception as exc:
        logger.debug("_publish_system_alert failed: %s", exc)


_CORR_KEY = "nexus:correlation:matrix"
_CORR_REFRESH_SEC = 300  # 5 minutes default

_DEFAULT_ASSETS: dict[str, str] = {
    "XAUUSD": "GC=F",
    "BTCUSD": "BTC-USD",
    "OIL":    "CL=F",
    "SPX":    "^GSPC",
}

_WINDOWS = {"7d": 7, "30d": 30}

# Historical "normal" correlation ranges (lo, hi) used for divergence detection.
# Pairs not listed here are monitored only for 7d-vs-30d shift, not norm violation.
_NORM_RANGES: dict[tuple[str, str], tuple[float, float]] = {
    ("XAUUSD", "BTCUSD"): (-0.1,  0.5),
    ("XAUUSD", "OIL"):    ( 0.0,  0.6),
    ("XAUUSD", "SPX"):    (-0.4,  0.2),
    ("BTCUSD", "OIL"):    (-0.1,  0.5),
    ("BTCUSD", "SPX"):    ( 0.2,  0.8),
    ("OIL",    "SPX"):    ( 0.1,  0.6),
}


# ── Price fetching ────────────────────────────────────────────────────────────

def _fetch_prices_sync(assets: dict[str, str], window_days: int = 40):
    """Download daily closing prices via yfinance. Returns pd.DataFrame or None."""
    try:
        import pandas as pd
        import yfinance as yf

        tickers = list(assets.values())
        period = f"{window_days + 10}d"
        raw = yf.download(tickers, period=period, interval="1d", progress=False, auto_adjust=True)
        if raw is None or raw.empty:
            return None

        # yfinance multi-ticker → MultiIndex columns (metric, ticker)
        if isinstance(raw.columns, pd.MultiIndex):
            closes = raw["Close"].copy()
        else:
            # Single ticker — wrap in named column
            closes = raw[["Close"]].copy()
            closes.columns = tickers

        # Rename tickers → asset names
        ticker_to_name = {v: k for k, v in assets.items()}
        closes = closes.rename(columns=ticker_to_name)
        known = [c for c in closes.columns if c in assets]
        if not known:
            return None
        closes = closes[known].dropna(how="all").ffill().bfill()
        return closes
    except Exception as exc:
        logger.warning("correlation._fetch_prices error: %s", exc)
        return None


# ── Matrix computation ────────────────────────────────────────────────────────

def _compute_corr_matrix(prices, window: int) -> dict[str, dict[str, float | None]]:
    """Pearson correlation on daily pct_change for the last `window` rows."""
    subset = prices.tail(window) if len(prices) >= window else prices
    rets = subset.pct_change().dropna()
    if rets.empty or len(rets) < 3:
        return {}
    corr = rets.corr()
    result: dict[str, dict[str, float | None]] = {}
    for a in corr.index:
        result[str(a)] = {}
        for b in corr.columns:
            val = corr.loc[a, b]
            result[str(a)][str(b)] = round(float(val), 3) if np.isfinite(val) else None
    return result


# ── Divergence detection ──────────────────────────────────────────────────────

def _detect_divergences(
    mat7: dict[str, dict],
    mat30: dict[str, dict],
    threshold: float = 0.25,
) -> list[dict]:
    """Flag pairs where 7d correlation deviates significantly from 30d baseline."""
    divs: list[dict] = []
    assets = list(mat7.keys())

    for i, a in enumerate(assets):
        for b in assets[i + 1:]:
            c7  = mat7.get(a, {}).get(b)
            c30 = mat30.get(a, {}).get(b)
            if c7 is None or c30 is None:
                continue

            shift = round(abs(c7 - c30), 3)
            norm  = _NORM_RANGES.get((a, b)) or _NORM_RANGES.get((b, a))
            out_of_norm = norm and (c7 < norm[0] - 0.15 or c7 > norm[1] + 0.15)

            if shift >= threshold or out_of_norm:
                direction = "creciente" if c7 > c30 else "decreciente"
                severity  = "HIGH" if (shift > 0.45 or out_of_norm) else "MEDIUM"
                divs.append({
                    "pair":        f"{a}/{b}",
                    "corr_7d":     c7,
                    "corr_30d":    c30,
                    "shift":       shift,
                    "severity":    severity,
                    "description": f"{a}/{b}: correlación {direction} ({c30:+.2f}→{c7:+.2f})",
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                })

    return sorted(divs, key=lambda x: x["shift"], reverse=True)


# ── Redis helpers ─────────────────────────────────────────────────────────────

async def _get_assets(redis_client) -> dict[str, str]:
    """Read corr_assets from runtime settings; fall back to defaults."""
    try:
        raw = await redis_client.get("nexus:runtime:settings")
        if raw:
            settings = json.loads(raw)
            custom = settings.get("corr_assets")
            if isinstance(custom, dict) and custom:
                return custom
    except Exception:
        pass
    return dict(_DEFAULT_ASSETS)


async def _get_threshold(redis_client) -> float:
    try:
        raw = await redis_client.get("nexus:runtime:settings")
        if raw:
            s = json.loads(raw)
            return float(s.get("corr_divergence_threshold", 0.25))
    except Exception:
        pass
    return 0.25


# ── Core refresh ──────────────────────────────────────────────────────────────

async def _run_correlation(redis_client) -> None:
    try:
        assets    = await _get_assets(redis_client)
        threshold = await _get_threshold(redis_client)
        loop      = asyncio.get_event_loop()

        prices = await loop.run_in_executor(None, _fetch_prices_sync, assets, 40)
        if prices is None or prices.empty:
            logger.warning("correlation: no price data available, skipping update")
            return

        mat7  = _compute_corr_matrix(prices, 7)
        mat30 = _compute_corr_matrix(prices, 30)
        divs  = _detect_divergences(mat7, mat30, threshold)

        payload = {
            "assets":      list(assets.keys()),
            "matrices":    {"7d": mat7, "30d": mat30},
            "divergences": divs,
            "updated_at":  datetime.now(timezone.utc).isoformat(),
        }
        await redis_client.set(_CORR_KEY, json.dumps(payload), ex=3600)
        logger.debug(
            "correlation matrix updated: %d assets, %d divergences",
            len(assets), len(divs),
        )

        # Publish new HIGH divergences not yet seen this session
        global _published_div_keys
        for div in divs:
            if div["severity"] == "HIGH":
                key = f"{div['pair']}:{div['corr_7d']:.2f}"
                if key not in _published_div_keys:
                    await _publish_system_alert(
                        redis_client, "CORR_DIVERGENCE",
                        div["description"],
                        severity="HIGH",
                        pair=div["pair"],
                        corr_7d=div["corr_7d"],
                        corr_30d=div["corr_30d"],
                        shift=div["shift"],
                    )
                    _published_div_keys.add(key)
                    if len(_published_div_keys) > 100:
                        _published_div_keys.clear()
    except Exception:
        logger.warning("correlation._run_correlation error", exc_info=True)


# ── Background loop ───────────────────────────────────────────────────────────

async def correlation_refresh_loop(redis_client, refresh_sec: int = _CORR_REFRESH_SEC) -> None:
    """Background task: compute correlation matrix on startup then every refresh_sec."""
    logger.info("Correlation matrix loop started (refresh=%ds)", refresh_sec)
    await _run_correlation(redis_client)
    while True:
        try:
            await asyncio.sleep(refresh_sec)
            await _run_correlation(redis_client)
        except asyncio.CancelledError:
            logger.info("Correlation loop cancelled")
            return
        except Exception:
            logger.warning("Correlation loop unexpected error", exc_info=True)
