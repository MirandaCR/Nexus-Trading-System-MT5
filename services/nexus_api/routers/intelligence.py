"""
Intelligence Router — signal quality, model performance by regime, GenAI brief,
and dynamic settings read/write for the dashboard.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from ..http_client import new_client
import redis.asyncio as aioredis
from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
_AGENTS_URL = os.getenv("NEXUS_AGENTS_URL", "http://localhost:8004")

_redis: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True, max_connections=5)
    return _redis


router = APIRouter(prefix="/api/intelligence", tags=["Intelligence"])


# ── Signal Quality ────────────────────────────────────────────────────────────

@router.get("/quality")
async def get_signal_quality():
    """Latest signal quality score and filter breakdown."""
    try:
        r = await _get_redis()
        raw = await r.get("nexus:signal:quality:last")
        if raw:
            return json.loads(raw)
        return {
            "score": 0,
            "max_score": 100,
            "threshold": 60,
            "passes": False,
            "breakdown": {},
            "computed_at": None,
        }
    except Exception:
        return {"error": "redis unavailable"}


# ── Model Performance by Regime ───────────────────────────────────────────────

@router.get("/models-by-regime")
async def get_models_by_regime():
    """WR and trade count for each model broken down by regime.

    Read from TradeMemory metrics (by_regime + by_source cross-reference)
    and the autonomous ML stats.
    """
    try:
        r = await _get_redis()
        raw_metrics = await r.get("nexus:trade_memory:metrics")
        if not raw_metrics:
            return {"by_regime": {}, "by_source": {}, "by_session": {}, "by_hour": {}, "overall": None}
        metrics = json.loads(raw_metrics)
        return {
            "by_regime": metrics.get("by_regime", {}),
            "by_source": metrics.get("by_source", {}),
            "by_session": metrics.get("by_session", {}),
            "by_hour": metrics.get("by_hour", {}),
            "overall": {
                "win_rate": metrics.get("overall_win_rate", 0),
                "total_trades": metrics.get("total_trades", 0),
                "total_wins": metrics.get("total_wins", 0),
                "total_losses": metrics.get("total_losses", 0),
                "avg_pnl_pips": metrics.get("avg_pnl_pips", 0),
                "recent_streak": metrics.get("recent_streak", {}),
            },
        }
    except Exception:
        logger.debug("models-by-regime error", exc_info=True)
        return {"by_regime": {}, "by_source": {}, "by_session": {}, "by_hour": {}, "overall": None}


# ── GenAI Daily Brief ─────────────────────────────────────────────────────────

@router.get("/genai-brief")
async def get_genai_brief():
    """Latest GenAI daily macro brief."""
    try:
        r = await _get_redis()
        raw = await r.get("nexus:genai:daily_bias")
        if raw:
            return json.loads(raw)
        return {
            "bias": "NEUTRAL",
            "summary": "No brief generated yet. System will produce one at 06:00 UTC.",
            "events": [],
            "updated_at": None,
        }
    except Exception:
        return {"bias": "NEUTRAL", "error": "redis unavailable"}


@router.get("/mtf-context/{symbol}")
async def get_mtf_context(symbol: str):
    """Multi-timeframe features and confluence score for a symbol."""
    try:
        r = await _get_redis()
        timeframes = ["M5", "M15", "H1", "H4", "D1"]
        result: dict[str, Any] = {}
        for tf in timeframes:
            raw = await r.get(f"nexus:features:{symbol}:{tf}")
            if raw:
                data = json.loads(raw)
                feats = data.get("features", data) if isinstance(data, dict) else {}
                # Compact summary for each TF.
                ema9 = feats.get("ema_9", 0)
                ema21 = feats.get("ema_21", 0)
                ema50 = feats.get("ema_50", 0)
                close = feats.get("close", 0)
                rsi = feats.get("rsi", 50)
                macd_hist = feats.get("macd_histogram", 0)
                adx = feats.get("adx", 0)
                atr = feats.get("atr_14", feats.get("atr", 0))
                bb_pct = feats.get("bb_pct_b", 0.5)

                if ema9 > ema21 > ema50 and macd_hist > 0:
                    bias = "BULLISH"
                elif ema9 < ema21 < ema50 and macd_hist < 0:
                    bias = "BEARISH"
                else:
                    bias = "NEUTRAL"

                result[tf] = {
                    "bias": bias,
                    "close": round(float(close), 5),
                    "rsi": round(float(rsi), 1),
                    "adx": round(float(adx), 1),
                    "macd_hist": round(float(macd_hist), 6),
                    "atr": round(float(atr), 4),
                    "bb_pct_b": round(float(bb_pct), 3),
                    "ema_stack": {
                        "ema9": round(float(ema9), 4),
                        "ema21": round(float(ema21), 4),
                        "ema50": round(float(ema50), 4),
                    },
                }

        # Compute confluence score (-4 to +4).
        bull = sum(1 for tf in result if result[tf]["bias"] == "BULLISH")
        bear = sum(1 for tf in result if result[tf]["bias"] == "BEARISH")
        total = len(result)
        alignment = bull - bear
        overall_bias = "BULLISH" if alignment >= 2 else "BEARISH" if alignment <= -2 else "NEUTRAL"

        return {
            "symbol": symbol,
            "timeframes": result,
            "confluence": {
                "alignment_score": alignment,
                "bull_count": bull,
                "bear_count": bear,
                "total_tfs": total,
                "overall_bias": overall_bias,
            },
        }
    except Exception:
        logger.debug("mtf-context error", exc_info=True)
        return {"symbol": symbol, "timeframes": {}, "confluence": {}}


# ── Dynamic Settings ──────────────────────────────────────────────────────────

class DynamicSettingsUpdate(BaseModel):
    settings: dict[str, Any]


@router.get("/settings")
async def get_dynamic_settings():
    """Read all user-configurable runtime settings."""
    try:
        r = await _get_redis()
        raw = await r.get("nexus:runtime:settings")
        if raw:
            return {"settings": json.loads(raw), "source": "redis"}
        return {"settings": {}, "source": "defaults"}
    except Exception:
        return {"settings": {}, "error": "redis unavailable"}


@router.post("/settings")
async def update_dynamic_settings(req: DynamicSettingsUpdate):
    """Write runtime settings to Redis. All services read from here."""
    try:
        r = await _get_redis()
        # Merge with existing settings.
        existing_raw = await r.get("nexus:runtime:settings")
        existing: dict[str, Any] = json.loads(existing_raw) if existing_raw else {}
        existing.update(req.settings)
        await r.set("nexus:runtime:settings", json.dumps(existing), ex=86400 * 7)
        return {"status": "saved", "settings": existing}
    except Exception:
        logger.error("Failed to save dynamic settings", exc_info=True)
        return {"status": "error", "message": "failed to save"}


# ── Cross-Asset Context ───────────────────────────────────────────────────────

@router.get("/cross-asset")
async def get_cross_asset():
    """Current DXY/US10Y cross-asset context for gold trading."""
    try:
        r = await _get_redis()
        raw = await r.get("nexus:cross_asset:summary")
        if raw:
            return json.loads(raw)
        # Fallback: read individual keys
        dxy_raw = await r.get("nexus:cross_asset:DXY")
        us10y_raw = await r.get("nexus:cross_asset:US10Y")
        return {
            "dxy": json.loads(dxy_raw) if dxy_raw else {"level": None, "source": "no_data"},
            "us10y": json.loads(us10y_raw) if us10y_raw else {"level": None, "source": "no_data"},
            "gold_yield_regime": "neutral",
            "gold_dxy_divergence": 0,
            "cross_asset_signal_pts": 3,
            "updated_at": None,
        }
    except Exception:
        return {"error": "redis unavailable"}


@router.get("/asset-profile/{symbol}")
async def get_asset_profile(symbol: str):
    """Trading parameters for a given symbol (from AssetProfile)."""
    try:
        from shared.asset_profile import get_profile, profile_to_dict
        profile = get_profile(symbol)
        return profile_to_dict(profile)
    except Exception as e:
        return {"error": str(e)}


@router.get("/shift-alerts")
async def get_shift_alerts(limit: int = 10):
    """Recent intraday cross-asset shift alerts (DXY/US10Y/regime changes)."""
    try:
        r = await _get_redis()
        raw = await r.lrange("nexus:cross_asset:shift_alerts", 0, limit - 1)
        return {"alerts": [json.loads(x) for x in raw], "count": len(raw)}
    except Exception:
        return {"alerts": [], "count": 0, "error": "redis unavailable"}


# ── News Sentinel (Fase 4) ────────────────────────────────────────────────────

@router.get("/news-alerts")
async def get_news_alerts(limit: int = 20):
    """Recent high/medium-impact news alerts from the sentinel."""
    try:
        r = await _get_redis()
        raw = await r.lrange("nexus:news:sentinel:alerts", 0, limit - 1)
        return {"alerts": [json.loads(x) for x in raw], "count": len(raw)}
    except Exception:
        return {"alerts": [], "count": 0, "error": "redis unavailable"}


@router.get("/news-blackout")
async def get_news_blackout():
    """Active blackout window status (HIGH-impact event ±30min)."""
    try:
        r = await _get_redis()
        raw = await r.get("nexus:news:sentinel:blackout")
        if raw:
            return json.loads(raw)
        return {"active": False, "reason": "no scan yet", "events": [], "ends_at": None}
    except Exception:
        return {"active": False, "error": "redis unavailable"}


@router.get("/brief-status")
async def get_brief_status():
    """Status of the daily brief service (last run, ok/error, next run)."""
    try:
        r = await _get_redis()
        status = await r.get("nexus:genai:brief:status") or "unknown"
        last_run = await r.get("nexus:genai:brief:last_run")
        return {"status": status, "last_run": last_run}
    except Exception:
        return {"status": "unknown", "error": "redis unavailable"}


# ── Correlation Matrix (Fase 5) ───────────────────────────────────────────────

@router.get("/correlation")
async def get_correlation():
    """Rolling 7d/30d asset correlation matrix (XAUUSD, BTCUSD, OIL, SPX)."""
    try:
        r = await _get_redis()
        raw = await r.get("nexus:correlation:matrix")
        if raw:
            return json.loads(raw)
        return {
            "assets": ["XAUUSD", "BTCUSD", "OIL", "SPX"],
            "matrices": {"7d": {}, "30d": {}},
            "divergences": [],
            "updated_at": None,
        }
    except Exception:
        return {"assets": [], "matrices": {}, "divergences": [], "error": "redis unavailable"}


# ── RL Lifecycle Actions ──────────────────────────────────────────────────────

@router.get("/lifecycle-actions")
async def get_lifecycle_actions():
    """Current RL lifecycle action recommendations for all open positions."""
    try:
        r = await _get_redis()
        raw = await r.get("nexus:rl:lifecycle:all")
        if raw:
            return {"actions": json.loads(raw), "source": "rl_lifecycle"}
        return {"actions": [], "source": "no_positions"}
    except Exception:
        return {"actions": [], "error": "redis unavailable"}


@router.post("/brief/trigger")
async def trigger_brief(symbol: str = "XAUUSD"):
    """Manually trigger a GenAI brief (proxies to nexus-agents)."""
    try:
        async with new_client(timeout=90.0) as client:
            resp = await client.post(
                f"{_AGENTS_URL}/api/agents/brief/generate",
                params={"symbol": symbol},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("brief trigger failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/lifecycle-actions/{ticket}")
async def get_lifecycle_action_for_ticket(ticket: int):
    """RL lifecycle action for a specific position ticket."""
    try:
        r = await _get_redis()
        raw = await r.get(f"nexus:rl:lifecycle:{ticket}")
        if raw:
            return json.loads(raw)
        return {"ticket": ticket, "action": "HOLD", "confidence": 0.0, "model": "not_found"}
    except Exception:
        return {"ticket": ticket, "action": "HOLD", "error": "redis unavailable"}


@router.get("/trade-memory")
async def get_trade_memory_full():
    """Full trade memory snapshot: metrics, thresholds, bias."""
    try:
        r = await _get_redis()
        metrics_raw = await r.get("nexus:trade_memory:metrics")
        thresholds_raw = await r.get("nexus:trade_memory:thresholds")
        trades_raw = await r.get("nexus:trade_memory:trades")
        return {
            "metrics": json.loads(metrics_raw) if metrics_raw else {},
            "thresholds": json.loads(thresholds_raw) if thresholds_raw else {},
            "recent_trades": json.loads(trades_raw)[-20:] if trades_raw else [],
        }
    except Exception:
        return {"metrics": {}, "thresholds": {}, "recent_trades": []}



@router.get("/institutional/{symbol}")
async def get_institutional_features(symbol: str):
    """
    Pre-computed institutional features: Hurst, Kelly ergódico, Lyapunov, etc.
    Calculados cada 60s por nexus_features. TTL Redis 300s.
    """
    try:
        r = await _get_redis()
        key = f"nexus:features:institutional:{symbol.upper()}"
        raw = await r.get(key)
        if raw:
            return {"status": "ok", "symbol": symbol.upper(), "data": json.loads(raw)}
        # Fallback: proxy to nexus_features directly
        features_url = os.getenv(
            "NEXUS_FEATURES_URL", "http://localhost:8002"
        )
        async with new_client(timeout=5.0) as client:
            resp = await client.get(
                f"{features_url}/api/features/institutional/{symbol.upper()}"
            )
            if resp.status_code == 200:
                return resp.json()
        return {"status": "calculating", "symbol": symbol.upper(), "data": None}
    except Exception as exc:
        logger.warning("institutional_features.error: %s", exc)
        return {"status": "error", "symbol": symbol.upper(), "data": None, "message": str(exc)}
