"""
REST API router for parallel model traders (ML / DL / RL).

Mounted alongside the existing ML service endpoints so nothing
in the original codebase is changed.  New endpoints live under
``/api/traders/...``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import numpy as np
import redis.asyncio as aioredis
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from shared.nexus_trades import build_nexus_comment
from .traders.base_trader import TradeSignal, ModelPerformance, get_pip_value
from .traders.ml_trader import MLTrader
from .traders.dl_trader import DLTrader
from .traders.rl_trader import RLTrader
from .traders.sl_tp_backtester import SLTPBacktester, SLTPBacktestResult
from .traders.trading_mode import (
    TradingMode, Session, TradingModeConfig,
    get_current_mode, get_current_config, get_rate_limiter, switch_mode,
    detect_session, compute_dynamic_sl_tp, compute_dynamic_lots,
    mtf_confirm_signal, spread_filter_ok, SESSION_CONFIGS,
    get_regime_policy, classify_trend_regime, market_quality_gate,
    DynamicTFSelection, select_operational_tf, get_confirmation_tfs_for,
)
from .traders.auto_retrain import AutoRetrainScheduler
from .traders.trade_memory import get_trade_memory, start_trade_memory, stop_trade_memory
from .traders.rl_lifecycle import RLLifecycleAgent, PositionState, ACTION_COLORS

try:
    from shared import runtime_settings
except Exception:  # pragma: no cover
    runtime_settings = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

MODEL_DIR = os.getenv("MODEL_DIR", "./models_store")
TRADERS_DIR = os.path.join(MODEL_DIR, "traders")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# ── Shared Redis connection pool (avoids connection leak) ────────────────────

_redis_pool: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    """Return a shared Redis connection (lazy-initialised, pooled)."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            REDIS_URL, decode_responses=True,
            max_connections=10,
        )
    return _redis_pool

router = APIRouter(prefix="/api/traders", tags=["Parallel Model Traders"])

# ── Runtime settings helper ──────────────────────────────────────────────────
# All parameters that were previously hardcoded can be overridden via
# POST /api/intelligence/settings → stored in nexus:runtime:settings in Redis.
# AssetProfile provides sensible per-asset defaults.

_SETTINGS_CACHE: dict[str, Any] = {}
_SETTINGS_CACHE_TS: float = 0.0
_SETTINGS_CACHE_TTL = 30.0  # re-read Redis every 30s


async def _get_runtime_setting(key: str, default: Any, symbol: str = "XAUUSD") -> Any:
    """Read a runtime setting from Redis (cached 30s). Falls back to AssetProfile, then default."""
    global _SETTINGS_CACHE, _SETTINGS_CACHE_TS
    now = time.time()
    if now - _SETTINGS_CACHE_TS > _SETTINGS_CACHE_TTL:
        try:
            r = await _get_redis()
            raw = await r.get("nexus:runtime:settings")
            if raw:
                _SETTINGS_CACHE = json.loads(raw)
            _SETTINGS_CACHE_TS = now
        except Exception:
            pass

    if key in _SETTINGS_CACHE:
        return _SETTINGS_CACHE[key]

    # AssetProfile fallback
    try:
        from shared.asset_profile import get_profile as _asset_profile
        profile = _asset_profile(symbol)
        profile_val = getattr(profile, key, None)
        if profile_val is not None:
            return profile_val
    except Exception:
        pass

    return default


# ── Directional Imbalance Tracker (Fix #7) ──────────────────────────────────
# Tracks last N signal directions to detect echo-chamber bias.
# If >80% of recent signals are same direction, penalise that direction.
_DIRECTION_HISTORY: list[str] = []  # last N directions ("BUY"/"SELL")
_DIRECTION_HISTORY_MAX = 15
_IMBALANCE_THRESHOLD = 0.80  # >80% same direction = imbalance
_IMBALANCE_CONV_PENALTY = 0.15  # reduce conviction of overrepresented side


def _record_direction(direction: str) -> None:
    """Record a trade direction for imbalance tracking."""
    if direction in ("BUY", "SELL"):
        _DIRECTION_HISTORY.append(direction)
        if len(_DIRECTION_HISTORY) > _DIRECTION_HISTORY_MAX:
            _DIRECTION_HISTORY.pop(0)


def _get_directional_imbalance() -> tuple[str | None, float]:
    """Return (overrepresented_side, ratio) or (None, 0) if balanced.

    When 12 of last 15 signals are SELL, returns ("SELL", 0.80).
    """
    if len(_DIRECTION_HISTORY) < 8:
        return None, 0.0
    buys = sum(1 for d in _DIRECTION_HISTORY if d == "BUY")
    sells = len(_DIRECTION_HISTORY) - buys
    total = len(_DIRECTION_HISTORY)
    if sells / total >= _IMBALANCE_THRESHOLD:
        return "SELL", sells / total
    if buys / total >= _IMBALANCE_THRESHOLD:
        return "BUY", buys / total
    return None, 0.0


# ── Singleton traders ────────────────────────────────────────────────────────

_ml_trader: MLTrader | None = None
_dl_trader: DLTrader | None = None
_rl_trader: RLTrader | None = None
_training_status: dict[str, Any] = {}
_retrain_scheduler: AutoRetrainScheduler | None = None
_autonomous_task: asyncio.Task | None = None
_autonomous_stats: dict[str, Any] = {
    "running": False,
    "trades_proposed": 0,
    "trades_approved": 0,
    "trades_rejected": 0,
    "last_cycle": "",
    "last_signal": {},
    "errors": 0,
}


def _get_ml_trader() -> MLTrader:
    global _ml_trader
    if _ml_trader is None:
        _ml_trader = MLTrader(model_dir=TRADERS_DIR)
        _ml_trader.load()
    return _ml_trader


def _get_dl_trader() -> DLTrader:
    global _dl_trader
    if _dl_trader is None:
        _dl_trader = DLTrader(model_dir=TRADERS_DIR)
        _dl_trader.load()
    return _dl_trader


def _get_rl_trader() -> RLTrader:
    global _rl_trader
    if _rl_trader is None:
        _rl_trader = RLTrader(model_dir=TRADERS_DIR)
        _rl_trader.load()
    return _rl_trader


# ── Request / Response Schemas ───────────────────────────────────────────────


class TraderStatusResponse(BaseModel):
    model_type: str
    model_name: str
    is_fitted: bool
    performance: dict[str, Any] = Field(default_factory=dict)


class AllTradersStatusResponse(BaseModel):
    ml: TraderStatusResponse
    dl: TraderStatusResponse
    rl: TraderStatusResponse
    training_in_progress: dict[str, bool] = Field(default_factory=dict)


class TradeSignalResponse(BaseModel):
    model_type: str
    model_name: str
    symbol: str
    direction: str
    confidence: float
    sl_pips: float
    tp_pips: float
    regime: str = "MEDIUM"
    reasoning: str = ""
    timestamp: str = ""


class AllSignalsResponse(BaseModel):
    ml: TradeSignalResponse
    dl: TradeSignalResponse
    rl: TradeSignalResponse
    consensus: TradeSignalResponse  # weighted consensus


class TrainRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["XAUUSD"])
    model_types: list[str] = Field(default_factory=lambda: ["ml", "dl", "rl"])
    timeframes: list[str] = Field(default_factory=lambda: ["M5"])


class TrainResponse(BaseModel):
    status: str
    message: str
    models_training: list[str] = Field(default_factory=list)


class ComparisonResponse(BaseModel):
    leaderboard: list[dict[str, Any]] = Field(default_factory=list)
    best_model: str = ""
    equity_curves: dict[str, list[float]] = Field(default_factory=dict)


class BacktestResponse(BaseModel):
    model_type: str
    result: dict[str, Any] = Field(default_factory=dict)


class SwitchModeRequest(BaseModel):
    mode: str = Field(description="'scalping' or 'swing'")


class ModeStatusResponse(BaseModel):
    current_mode: str
    config: dict[str, Any] = Field(default_factory=dict)
    session: str = ""
    retrain_scheduler: dict[str, Any] = Field(default_factory=dict)
    genai_rate_limiter: dict[str, Any] = Field(default_factory=dict)
    dynamic_tf: dict[str, Any] = Field(default_factory=dict)


_DYNAMIC_TF_KEY = "nexus:dynamic:tf"


async def _publish_dynamic_tf(selection: DynamicTFSelection) -> None:
    """Cache the live TF choice so ML signals + GenAI agents stay in sync."""
    from datetime import datetime, timezone as tz

    payload = {
        "operational_tf": selection.operational_tf,
        "confirmation_tfs": selection.confirmation_tfs,
        "base_tf": selection.base_tf,
        "reason": selection.reason,
        "vol_regime": selection.vol_regime,
        "session": selection.session,
        "auto_selected": selection.auto_selected,
        "updated_at": datetime.now(tz.utc).isoformat(),
    }
    try:
        r = await _get_redis()
        await r.set(_DYNAMIC_TF_KEY, json.dumps(payload), ex=180)
    except Exception:
        logger.debug("Failed to cache dynamic TF", exc_info=True)

    if runtime_settings is not None:
        try:
            await runtime_settings.update_settings({
                "operational_tf": selection.operational_tf,
            })
        except Exception:
            logger.debug("Failed to sync operational_tf to runtime settings", exc_info=True)


async def _read_dynamic_tf() -> dict[str, Any] | None:
    try:
        r = await _get_redis()
        raw = await r.get(_DYNAMIC_TF_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return None


async def _resolve_dynamic_tf(
    features: dict[str, Any],
    session: Session,
    cfg: TradingModeConfig,
    *,
    current_spread: float = 0.0,
    avg_spread: float = 0.0,
) -> DynamicTFSelection:
    auto = True
    if runtime_settings is not None:
        try:
            settings = await runtime_settings.get_settings()
            auto = bool(settings.get("auto_tf_selection", True))
        except Exception:
            pass
    selection = select_operational_tf(
        cfg,
        session,
        features,
        auto=auto,
        current_spread=current_spread,
        avg_spread=avg_spread,
    )
    await _publish_dynamic_tf(selection)
    return selection


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/status", response_model=AllTradersStatusResponse)
async def traders_status():
    """Get status of all parallel traders."""
    ml = _get_ml_trader()
    dl = _get_dl_trader()
    rl = _get_rl_trader()

    return AllTradersStatusResponse(
        ml=TraderStatusResponse(
            model_type="ml", model_name=ml.model_name,
            is_fitted=ml.is_fitted, performance=ml.performance.to_dict(),
        ),
        dl=TraderStatusResponse(
            model_type="dl", model_name=dl.model_name,
            is_fitted=dl.is_fitted, performance=dl.performance.to_dict(),
        ),
        rl=TraderStatusResponse(
            model_type="rl", model_name=rl.model_name,
            is_fitted=rl.is_fitted, performance=rl.performance.to_dict(),
        ),
        training_in_progress={
            "ml": _training_status.get("ml", False),
            "dl": _training_status.get("dl", False),
            "rl": _training_status.get("rl", False),
        },
    )


@router.get("/predict/{symbol}", response_model=AllSignalsResponse)
async def predict_all(symbol: str):
    """Get predictions from all traders for a symbol.

    Pipeline: raw signals → spread filter → MTF confirmation
              → dynamic SL/TP override → session check → consensus.
    """
    from datetime import datetime, timezone as tz

    features = await _get_latest_features(symbol)
    if features is None:
        raise HTTPException(404, f"No cached features for {symbol}")

    cfg = get_current_config()
    session = detect_session(datetime.now(tz.utc).hour)

    current_spread = float(features.get("spread", features.get("spread_pips", 0.0)))
    avg_spread = float(features.get("avg_spread", current_spread or 1.0))
    tf_sel = await _resolve_dynamic_tf(
        features, session, cfg,
        current_spread=current_spread, avg_spread=avg_spread,
    )
    features = await _get_latest_features(symbol, operational_tf=tf_sel.operational_tf) or features
    confirm_tfs = tf_sel.confirmation_tfs

    # ── Session gate: reject if trading not allowed ──
    sess_cfgs = SESSION_CONFIGS.get(cfg.mode, {})
    sess_cfg = sess_cfgs.get(session)
    if sess_cfg and sess_cfg.max_trades == 0:
        no_trade = TradeSignal(
            model_type="consensus", model_name="Session_Block",
            symbol=symbol, direction="NO_TRADE", confidence=0.0,
            session=session.value,
            reasoning=f"Session {session.value} blocked for {cfg.mode.value}",
        )
        return AllSignalsResponse(
            ml=_signal_to_response(no_trade),
            dl=_signal_to_response(no_trade),
            rl=_signal_to_response(no_trade),
            consensus=_signal_to_response(no_trade),
        )

    # ── Spread pre-filter ──
    current_spread = float(features.get("spread", features.get("spread_pips", 0.0)))
    avg_spread = float(features.get("avg_spread", current_spread or 1.0))
    spread_ok_flag, spread_reason = spread_filter_ok(current_spread, avg_spread, cfg, session)

    if not spread_ok_flag:
        no_trade = TradeSignal(
            model_type="consensus", model_name="Spread_Filter",
            symbol=symbol, direction="NO_TRADE", confidence=0.0,
            spread_ok=False, session=session.value,
            reasoning=spread_reason,
        )
        return AllSignalsResponse(
            ml=_signal_to_response(no_trade),
            dl=_signal_to_response(no_trade),
            rl=_signal_to_response(no_trade),
            consensus=_signal_to_response(no_trade),
        )

    # ── Raw predictions ──
    ml = _get_ml_trader()
    dl = _get_dl_trader()
    rl = _get_rl_trader()

    ml_sig = ml.predict(features, symbol)
    dl_sig = dl.predict(features, symbol)
    rl_sig = rl.predict(features, symbol)

    # ── Compute consensus ──
    consensus = _compute_consensus(ml_sig, dl_sig, rl_sig, symbol)

    # ── MTF confirmation (on consensus direction) ──
    if consensus.direction != "NO_TRADE":
        from dataclasses import replace
        cfg_mtf = replace(cfg, confirmation_tfs=confirm_tfs)
        htf_trends = await _get_htf_trends(symbol, confirm_tfs)
        mtf_ok, mtf_reason = mtf_confirm_signal(consensus.direction, htf_trends, cfg_mtf)
        if not mtf_ok:
            consensus = TradeSignal(
                model_type="consensus",
                model_name="MTF_Rejected",
                symbol=symbol,
                direction="NO_TRADE",
                confidence=consensus.confidence * 0.5,
                sl_pips=consensus.sl_pips,
                tp_pips=consensus.tp_pips,
                mtf_confirmed=False,
                session=session.value,
                reasoning=f"MTF rejected: {mtf_reason}",
            )
        else:
            # ── Dynamic SL/TP override ──
            atr = float(features.get("atr_14", features.get("atr", 0.0)))
            pip_value = get_pip_value(symbol)
            vol_regime = _detect_volatility_regime(features)
            sl_pips, tp_pips = compute_dynamic_sl_tp(
                atr=atr, pip_value=pip_value, config=cfg,
                session=session, volatility_regime=vol_regime,
            )
            consensus = TradeSignal(
                model_type=consensus.model_type,
                model_name=consensus.model_name,
                symbol=symbol,
                direction=consensus.direction,
                confidence=consensus.confidence,
                sl_pips=sl_pips,
                tp_pips=tp_pips,
                mtf_confirmed=True,
                spread_ok=True,
                session=session.value,
                reasoning=consensus.reasoning + f" | MTF OK | dyn SL={sl_pips} TP={tp_pips} [{vol_regime}]",
            )

    return AllSignalsResponse(
        ml=_signal_to_response(ml_sig),
        dl=_signal_to_response(dl_sig),
        rl=_signal_to_response(rl_sig),
        consensus=_signal_to_response(consensus),
    )


@router.get("/predict/{model_type}/{symbol}", response_model=TradeSignalResponse)
async def predict_single(model_type: str, symbol: str):
    """Get prediction from a specific trader."""
    features = await _get_latest_features(symbol)
    if features is None:
        raise HTTPException(404, f"No cached features for {symbol}")

    trader = _get_trader_by_type(model_type)
    signal = trader.predict(features, symbol)
    return _signal_to_response(signal)


@router.post("/train", response_model=TrainResponse)
async def train_traders(req: TrainRequest, background_tasks: BackgroundTasks):
    """Start training for specified model types."""
    active = [t for t in req.model_types if not _training_status.get(t, False)]
    if not active:
        raise HTTPException(409, "All requested models are already training")

    for model_type in active:
        _training_status[model_type] = True

    background_tasks.add_task(_run_training, active, req.symbols, req.timeframes)

    return TrainResponse(
        status="started",
        message=f"Training initiated for {active}",
        models_training=active,
    )


@router.get("/comparison", response_model=ComparisonResponse)
async def model_comparison():
    """Compare performance across all model types for leaderboard."""
    ml = _get_ml_trader()
    dl = _get_dl_trader()
    rl = _get_rl_trader()

    models = [
        ("GenAI Agents", _get_genai_performance()),
        ("ML (XGB+LGB)", ml.performance.to_dict()),
        ("Deep Learning (Transformer)", dl.performance.to_dict()),
        ("Reinforcement Learning (PPO)", rl.performance.to_dict()),
    ]

    leaderboard = []
    equity_curves: dict[str, list[float]] = {}

    for name, perf in models:
        entry = {
            "name": name,
            "model_type": perf.get("model_type", "genai"),
            "win_rate": perf.get("win_rate", 0.0),
            "profit_factor": perf.get("profit_factor", 0.0),
            "sharpe_ratio": perf.get("sharpe_ratio", 0.0),
            "max_drawdown": perf.get("max_drawdown", 0.0),
            "total_return": perf.get("total_return", 0.0),
            "total_trades": perf.get("total_trades", 0),
            "avg_rr": perf.get("avg_rr", 0.0),
            "is_fitted": perf.get("is_fitted", False),
            "last_trained": perf.get("last_trained", ""),
        }
        leaderboard.append(entry)
        eq = perf.get("equity_curve", [])
        if eq:
            equity_curves[name] = eq

    # Sort by Sharpe ratio descending
    leaderboard.sort(key=lambda x: x["sharpe_ratio"], reverse=True)
    best = leaderboard[0]["name"] if leaderboard else ""

    return ComparisonResponse(
        leaderboard=leaderboard,
        best_model=best,
        equity_curves=equity_curves,
    )


@router.get("/performance/{model_type}")
async def model_performance(model_type: str):
    """Get detailed performance for a specific model type."""
    trader = _get_trader_by_type(model_type)
    return trader.performance.to_dict()


@router.get("/signals/recent/{model_type}")
async def recent_signals(model_type: str, limit: int = 20):
    """Get recent signals from a specific trader."""
    trader = _get_trader_by_type(model_type)
    return {"signals": trader._recent_signals[-limit:]}


# ── Trading Mode Endpoints ──────────────────────────────────────────────────


@router.get("/mode", response_model=ModeStatusResponse)
async def get_trading_mode():
    """Get current trading mode, session, and scheduler status."""
    from datetime import datetime, timezone as tz
    cfg = get_current_config()
    session = detect_session(datetime.now(tz.utc).hour)
    limiter = get_rate_limiter()

    dynamic_tf = await _read_dynamic_tf() or {}

    return ModeStatusResponse(
        current_mode=get_current_mode().value,
        config={
            "operational_tf": dynamic_tf.get("operational_tf", cfg.operational_tf),
            "base_operational_tf": cfg.operational_tf,
            "confirmation_tfs": dynamic_tf.get("confirmation_tfs", cfg.confirmation_tfs),
            "sl_range": f"{cfg.sl_min}-{cfg.sl_max}",
            "tp_range": f"{cfg.tp_min}-{cfg.tp_max}",
            "max_hold_bars": cfg.max_hold_bars,
            "risk_per_trade": cfg.risk_per_trade,
            "max_daily_trades": cfg.max_daily_trades,
            "retrain_interval": f"{cfg.retrain_interval_sec // 3600}h",
            "genai_budget": f"{cfg.genai_max_calls_per_hour}/hr",
            "pre_signal_spread_filter": cfg.pre_signal_spread_filter,
            "max_spread_pips": cfg.max_spread_pips,
            "ensemble_weights": cfg.ensemble_weights,
        },
        session=session.value,
        retrain_scheduler=_retrain_scheduler.stats if _retrain_scheduler else {},
        genai_rate_limiter={
            "calls_remaining": limiter.calls_remaining,
            "next_available_in": limiter.next_available_in,
            "enabled": limiter.enabled,
        },
        dynamic_tf=dynamic_tf,
    )


@router.post("/mode/switch")
async def switch_trading_mode(req: SwitchModeRequest):
    """Switch between SCALPING and SWING modes."""
    global _retrain_scheduler
    try:
        new_mode = TradingMode(req.mode.lower())
    except ValueError:
        raise HTTPException(400, f"Invalid mode: {req.mode}. Use 'scalping' or 'swing'.")

    config = switch_mode(new_mode)

    # Sync operational TF to Redis runtime settings (agents + risk read this)
    if runtime_settings is not None:
        try:
            await runtime_settings.update_settings({
                "operational_tf": config.operational_tf,
                "trading_mode_type": new_mode.value,
            })
        except Exception:
            logger.warning("Failed to sync operational_tf to runtime settings", exc_info=True)

    # Restart retrain scheduler with new interval
    if _retrain_scheduler:
        _retrain_scheduler.stop()
    _retrain_scheduler = AutoRetrainScheduler(
        retrain_interval_sec=config.retrain_interval_sec,
        retrain_callback=_auto_retrain_callback,
    )
    _retrain_scheduler.start()

    return {
        "status": "switched",
        "mode": new_mode.value,
        "operational_tf": config.operational_tf,
        "retrain_interval": f"{config.retrain_interval_sec // 3600}h",
    }


@router.post("/retrain/force")
async def force_retrain():
    """Force an immediate model retrain."""
    if _retrain_scheduler:
        _retrain_scheduler.force_retrain()
        return {"status": "forced", "message": "Retrain will execute on next scheduler tick"}
    raise HTTPException(400, "Retrain scheduler not running")


@router.get("/dynamic-params/{symbol}")
async def get_dynamic_params(symbol: str):
    """Get current dynamic SL/TP/Lots for a symbol based on market conditions."""
    from datetime import datetime, timezone as tz
    cfg = get_current_config()
    session = detect_session(datetime.now(tz.utc).hour)

    features = await _get_latest_features(symbol)
    atr = 0.0
    if features:
        atr = features.get("atr_14", features.get("atr", 0.0))

    pip_value = get_pip_value(symbol)
    sl_pips, tp_pips = compute_dynamic_sl_tp(
        atr=atr, pip_value=pip_value, config=cfg,
        session=session, volatility_regime="MEDIUM",
    )
    balance = await _get_account_balance()
    lots = compute_dynamic_lots(
        balance=balance, sl_pips=sl_pips, config=cfg,
        session=session, confidence=0.7,
    )

    spread_ok_flag, spread_reason = True, "No spread data"
    sess_cfg = SESSION_CONFIGS.get(cfg.mode, {}).get(session)

    return {
        "symbol": symbol,
        "mode": cfg.mode.value,
        "session": session.value,
        "atr": round(atr, 6),
        "sl_pips": sl_pips,
        "tp_pips": tp_pips,
        "lot_size": lots,
        "rr_ratio": round(tp_pips / sl_pips, 2) if sl_pips > 0 else 0,
        "spread_ok": spread_ok_flag,
        "spread_reason": spread_reason,
        "session_config": {
            "liquidity": sess_cfg.liquidity_factor if sess_cfg else 0,
            "size_multiplier": sess_cfg.size_multiplier if sess_cfg else 0,
            "scalping_allowed": sess_cfg.scalping_allowed if sess_cfg else False,
        } if sess_cfg else {},
    }


# ── Autonomous ML Trading Pipeline ──────────────────────────────────────────
# Independent background loop: features → ML/DL/RL consensus → risk → execute
# Runs in PARALLEL with the GenAI agent pipeline, NOT replacing it.


@router.get("/memory")
async def trade_memory_status():
    """Get trade memory metrics and current bias snapshot."""
    memory = get_trade_memory()
    snapshot = await memory.get_memory_snapshot()
    from datetime import datetime, timezone as tz
    session = detect_session(datetime.now(tz.utc).hour)
    bias = await memory.get_trade_bias(session=session.value)
    return {
        "snapshot": snapshot,
        "current_bias": bias.to_dict(),
        "session": session.value,
    }


@router.get("/autonomous/status")
async def autonomous_status():
    """Get status of the autonomous ML trading pipeline."""
    return {
        "running": _autonomous_stats["running"],
        "trades_proposed": _autonomous_stats["trades_proposed"],
        "trades_approved": _autonomous_stats["trades_approved"],
        "trades_rejected": _autonomous_stats["trades_rejected"],
        "last_cycle": _autonomous_stats["last_cycle"],
        "last_signal": _autonomous_stats["last_signal"],
        "errors": _autonomous_stats["errors"],
        "mode": get_current_mode().value,
        "dynamic_tf": await _read_dynamic_tf() or {},
    }


@router.post("/autonomous/toggle")
async def toggle_autonomous(enable: bool = True):
    """Enable or disable the autonomous ML trading pipeline."""
    global _autonomous_task
    if enable and (_autonomous_task is None or _autonomous_task.done()):
        _autonomous_task = asyncio.create_task(_autonomous_trading_loop())
        _autonomous_stats["running"] = True
        return {"status": "started", "message": "Autonomous ML pipeline started"}
    elif not enable and _autonomous_task and not _autonomous_task.done():
        _autonomous_task.cancel()
        _autonomous_stats["running"] = False
        return {"status": "stopped", "message": "Autonomous ML pipeline stopped"}
    return {"status": "no_change"}


async def _autonomous_trading_loop() -> None:
    """Background loop: poll features → predict → risk check → propose trade.

    This runs independently of GenAI agents and publishes trade proposals
    directly to the risk service, creating a fully autonomous ML trading path.
    """
    import httpx

    symbols = os.getenv("STREAM_SYMBOLS", "XAUUSD").split(",")
    risk_url = os.getenv("NEXUS_RISK_URL", "http://localhost:8006")
    executor_url = os.getenv("NEXUS_EXECUTOR_URL", "http://localhost:8007")

    logger.info("Autonomous ML pipeline started for symbols=%s", symbols)
    _autonomous_stats["running"] = True

    while True:
        try:
            cfg = get_current_config()

            # Refresh front-configurable settings into the process cache so the
            # (sync) consensus + MTF logic see the latest values. Also apply the
            # configurable multi-timeframe agreement requirement.
            if runtime_settings is not None:
                try:
                    _s = await runtime_settings.get_settings()
                    if _s.get("require_mtf_confirmation"):
                        cfg.min_mtf_agreement = max(1, int(_s.get("min_mtf_agreement", 1)))
                    else:
                        cfg.min_mtf_agreement = 0
                except Exception:
                    pass

            from datetime import datetime, timezone as tz
            session = detect_session(datetime.now(tz.utc).hour)

            # Respect session gating
            sess_cfgs = SESSION_CONFIGS.get(cfg.mode, {})
            sess_cfg = sess_cfgs.get(session)
            if sess_cfg and sess_cfg.max_trades == 0:
                _autonomous_stats["last_cycle"] = datetime.now(tz.utc).isoformat()
                await asyncio.sleep(30)
                continue

            for symbol in symbols:
                try:
                    await _autonomous_cycle(symbol, cfg, session, risk_url, executor_url)
                except Exception:
                    logger.error("Autonomous cycle error for %s", symbol, exc_info=True)
                    _autonomous_stats["errors"] += 1

            _autonomous_stats["last_cycle"] = datetime.now(tz.utc).isoformat()

            # Cycle interval follows the live dynamic TF
            dyn = await _read_dynamic_tf()
            eff_tf = (dyn or {}).get("operational_tf", cfg.operational_tf)
            if eff_tf == "M1":
                await asyncio.sleep(30)
            elif eff_tf == "M5":
                await asyncio.sleep(60)
            elif eff_tf == "M15":
                await asyncio.sleep(120)
            else:
                await asyncio.sleep(300)

        except asyncio.CancelledError:
            logger.info("Autonomous ML pipeline cancelled")
            _autonomous_stats["running"] = False
            return
        except Exception:
            logger.error("Autonomous pipeline error", exc_info=True)
            _autonomous_stats["errors"] += 1
            await asyncio.sleep(30)


async def _autonomous_cycle(
    symbol: str,
    cfg: TradingModeConfig,
    session: Session,
    risk_url: str,
    executor_url: str,
) -> None:
    """Single autonomous cycle for one symbol."""
    import httpx
    from datetime import datetime, timezone as tz

    # 1. Fetch features + resolve dynamic operational TF
    features_probe = await _get_latest_features(symbol)
    if features_probe is None:
        return

    current_spread = float(features_probe.get("spread", features_probe.get("spread_pips", 0.0)))
    avg_spread = float(features_probe.get("avg_spread", current_spread or 1.0))
    tf_sel = await _resolve_dynamic_tf(
        features_probe, session, cfg,
        current_spread=current_spread, avg_spread=avg_spread,
    )
    _autonomous_stats["dynamic_tf"] = {
        "operational_tf": tf_sel.operational_tf,
        "confirmation_tfs": tf_sel.confirmation_tfs,
        "reason": tf_sel.reason,
        "vol_regime": tf_sel.vol_regime,
        "auto_selected": tf_sel.auto_selected,
    }
    features = await _get_latest_features(symbol, operational_tf=tf_sel.operational_tf) or features_probe
    confirm_tfs = tf_sel.confirmation_tfs

    # 2. Spread pre-filter
    spread_ok_flag, _ = spread_filter_ok(current_spread, avg_spread, cfg, session)
    if not spread_ok_flag:
        return

    # 2b. Market-quality gate — skip dead / illiquid markets and blown spreads.
    quality_ok, quality_reason = market_quality_gate(
        features, current_spread=current_spread, avg_spread=avg_spread,
    )
    if not quality_ok:
        logger.info("Autonomous ML SKIP %s — %s", symbol, quality_reason)
        return

    # 2c. Publish current regime to Redis (dashboard reads this in real-time).
    regime_info = await _publish_regime(symbol, features)
    final_regime = regime_info.get("regime", "UNKNOWN")

    # 3. Raw predictions from all models
    ml = _get_ml_trader()
    dl = _get_dl_trader()
    rl = _get_rl_trader()

    ml_sig = ml.predict(features, symbol)
    dl_sig = dl.predict(features, symbol)
    rl_sig = rl.predict(features, symbol)

    # 4. Compute consensus
    consensus = _compute_consensus(ml_sig, dl_sig, rl_sig, symbol)

    if consensus.direction == "NO_TRADE":
        # ── Fix #7: Contrarian ranging override ──────────────────────────
        # If consensus is NO_TRADE but we're in a RANGING regime with price
        # at a Bollinger extreme, generate a mean-reversion signal. This
        # allows the system to BUY at the bottom even when all ML models
        # are biased SELL.
        _regime_check = classify_trend_regime(features)
        if _regime_check == "RANGING":
            bb_up = float(features.get("bb_upper", 0) or 0)
            bb_lo = float(features.get("bb_lower", 0) or 0)
            close_p = float(features.get("close", 0) or 0)
            rsi = float(features.get("rsi_14", features.get("rsi", 50)) or 50)
            if bb_up > bb_lo > 0 and close_p > 0:
                pos = (close_p - bb_lo) / (bb_up - bb_lo)
                # Near bottom + RSI oversold → contrarian BUY
                if pos <= 0.20 and rsi < 35:
                    consensus = TradeSignal(
                        model_type="consensus", model_name="Contrarian_Ranging",
                        symbol=symbol, direction="BUY",
                        confidence=0.65, sl_pips=0, tp_pips=0,
                        session=session.value,
                        reasoning=f"Contrarian BUY: ranging pos={pos:.0%} RSI={rsi:.1f}",
                    )
                    logger.info("Contrarian ranging BUY override: pos=%.2f RSI=%.1f", pos, rsi)
                # Near top + RSI overbought → contrarian SELL
                elif pos >= 0.80 and rsi > 65:
                    consensus = TradeSignal(
                        model_type="consensus", model_name="Contrarian_Ranging",
                        symbol=symbol, direction="SELL",
                        confidence=0.65, sl_pips=0, tp_pips=0,
                        session=session.value,
                        reasoning=f"Contrarian SELL: ranging pos={pos:.0%} RSI={rsi:.1f}",
                    )
                    logger.info("Contrarian ranging SELL override: pos=%.2f RSI=%.1f", pos, rsi)
                else:
                    return
            else:
                return
        else:
            return

    # ── Fix #7: Directional imbalance penalty ────────────────────────────
    # If >80% of recent trades were same direction, penalise that direction
    # to force diversity. This breaks the echo-chamber when all 3 models
    # learn the same bias from recent data.
    imb_side, imb_ratio = _get_directional_imbalance()
    if imb_side and consensus.direction == imb_side:
        penalty = _IMBALANCE_CONV_PENALTY * (imb_ratio - _IMBALANCE_THRESHOLD + 0.20)
        new_conf = consensus.confidence - penalty
        logger.info(
            "Directional imbalance: %s %.0f%% of last %d — penalising conv %.3f → %.3f",
            imb_side, imb_ratio * 100, len(_DIRECTION_HISTORY),
            consensus.confidence, new_conf,
        )
        consensus = TradeSignal(
            model_type=consensus.model_type, model_name=consensus.model_name,
            symbol=symbol, direction=consensus.direction,
            confidence=new_conf,
            sl_pips=consensus.sl_pips, tp_pips=consensus.tp_pips,
            session=consensus.session,
            reasoning=consensus.reasoning + f" | Imbalance penalty {imb_side} {imb_ratio:.0%}",
        )

    # 4a. DYNAMIC REGIME POLICY — adapt the gates to the current regime.
    trend_regime = classify_trend_regime(features)
    sess_cfgs2 = SESSION_CONFIGS.get(cfg.mode, {})
    sess_cfg2 = sess_cfgs2.get(session)
    _conv_fallback = await _get_runtime_setting("conviction_min", 0.55, symbol)
    base_conv = sess_cfg2.min_conviction if sess_cfg2 else _conv_fallback
    policy = get_regime_policy(trend_regime, base_min_conviction=base_conv, base_min_rr=cfg.min_rr_ratio)

    # (i) Regime conviction gate — demand more in choppy/unknown regimes.
    if consensus.confidence < policy.min_conviction:
        logger.info(
            "Autonomous ML NO_TRADE %s [%s]: conv %.3f < regime-min %.3f (%s)",
            symbol, trend_regime, consensus.confidence, policy.min_conviction, policy.reason,
        )
        return

    # (ii) RANGING → only fade extremes (mean-reversion). SELL near the top of
    #      the band, BUY near the bottom; refuse trades in the middle of a range
    #      (the exact pattern that kept getting stopped out).
    if policy.mean_reversion_only:
        bb_upper = float(features.get("bb_upper", 0) or 0)
        bb_lower = float(features.get("bb_lower", 0) or 0)
        close_px = float(features.get("close", 0) or 0)
        if bb_upper > bb_lower > 0 and close_px > 0:
            pos_in_range = (close_px - bb_lower) / (bb_upper - bb_lower)
            near_top = pos_in_range >= 0.75
            near_bottom = pos_in_range <= 0.25
            ok = (consensus.direction == "SELL" and near_top) or (
                consensus.direction == "BUY" and near_bottom
            )
            if not ok:
                logger.info(
                    "Autonomous ML NO_TRADE %s [RANGING]: %s but price mid-range "
                    "(pos=%.0f%%) — wait for extreme",
                    symbol, consensus.direction, pos_in_range * 100,
                )
                return
        else:
            return  # no band data → don't guess in a range

    # (iii) TRENDING → only trade WITH the higher-timeframe trend.
    if policy.require_trend_align:
        htf = await _get_htf_trends(symbol, confirm_tfs)
        bull = sum(1 for v in htf.values() if v == "BULLISH")
        bear = sum(1 for v in htf.values() if v == "BEARISH")
        trend_dir = "BUY" if bull > bear else "SELL" if bear > bull else "NONE"
        if trend_dir != "NONE" and consensus.direction != trend_dir:
            logger.info(
                "Autonomous ML NO_TRADE %s [TRENDING]: %s against HTF trend %s",
                symbol, consensus.direction, trend_dir,
            )
            return

    # 4b. Orchestrator — defer if GenAI agents have priority lock
    try:
        from shared.orchestrator import should_defer
        _r = await _get_redis()
        defer, reason = await should_defer(_r, symbol, "autonomous_ml")
        if defer:
            logger.debug("Autonomous ML deferring %s: %s", symbol, reason)
            return
    except Exception:
        pass

    # 4c. Trade memory bias — learn from past wins/losses
    try:
        memory = get_trade_memory()
        _regime_for_bias = _detect_volatility_regime(features)
        bias = await memory.get_trade_bias(
            session=session.value,
            direction=consensus.direction,
            regime=_regime_for_bias,
            source="autonomous_ml",
        )
        if bias.skip_cycle:
            logger.info("Autonomous ML skipped (memory): %s — %s", symbol, bias.reasons)
            return
        if bias.no_trade:
            logger.info("Autonomous ML NO_TRADE (memory): %s — %s", symbol, bias.reasons)
            return
        if bias.confidence_mult < 1.0:
            original_conf = consensus.confidence
            consensus = TradeSignal(
                model_type=consensus.model_type,
                model_name=consensus.model_name,
                symbol=symbol,
                direction=consensus.direction,
                confidence=consensus.confidence * bias.confidence_mult,
                sl_pips=consensus.sl_pips,
                tp_pips=consensus.tp_pips,
                session=consensus.session,
                reasoning=consensus.reasoning + f" | Memory adj: {', '.join(bias.reasons)}",
            )
            logger.info(
                "Trade memory adjusted confidence: %s %.3f → %.3f (%s)",
                symbol, original_conf, consensus.confidence, bias.reasons,
            )
            _min_conf_floor = await _get_runtime_setting("min_confidence_floor", 0.40, symbol)
            if consensus.direction != "NO_TRADE" and consensus.confidence < _min_conf_floor:
                logger.info("Autonomous ML confidence too low after memory adj: %s", symbol)
                return
    except Exception:
        logger.debug("Trade memory check failed (non-fatal)", exc_info=True)

    # 5. MTF confirmation (uses dynamic confirmation TFs)
    from dataclasses import replace
    cfg_mtf = replace(cfg, confirmation_tfs=confirm_tfs)
    htf_trends = await _get_htf_trends(symbol, confirm_tfs)
    mtf_ok, mtf_reason = mtf_confirm_signal(consensus.direction, htf_trends, cfg_mtf)
    if not mtf_ok:
        return

    # 5b. Signal quality gate — 8 dynamic filters, scored 0-100.
    try:
        q_score, q_payload = await _compute_signal_quality(
            symbol=symbol,
            features=features,
            direction=consensus.direction,
            regime_info=regime_info,
            htf_trends=htf_trends,
            session_value=session.value,
            current_spread=current_spread,
            avg_spread=avg_spread,
        )
        mem = get_trade_memory()
        thresholds = await mem.get_dynamic_thresholds()
        q_threshold = thresholds.get("quality_score_threshold", 60)
        if q_score < q_threshold:
            logger.info(
                "Autonomous ML BLOCKED by quality gate: %s %s score=%d < threshold=%d",
                symbol, consensus.direction, q_score, q_threshold,
            )
            return
        logger.debug("Signal quality OK: %s score=%d/%d", symbol, q_score, 100)
    except Exception:
        logger.debug("Signal quality gate failed (non-fatal)", exc_info=True)

    # 6. Dynamic SL/TP
    atr = float(features.get("atr_14", features.get("atr", 0.0)))
    pip_value = get_pip_value(symbol)
    vol_regime = _detect_volatility_regime(features)
    sl_pips, tp_pips = compute_dynamic_sl_tp(
        atr=atr, pip_value=pip_value, config=cfg,
        session=session, volatility_regime=vol_regime,
    )

    # ── SL floor (configurable ATR multiplier) ───────────────────────────
    # Gold's ATR(M5)≈$4. A 1.5× ATR SL ($6) gets eaten by normal tick noise.
    # Default 2.5× ATR gives trades room to breathe; configurable via settings.
    _sl_floor_mult = await _get_runtime_setting("sl_floor_atr_mult", 2.5, symbol)
    if atr > 0 and pip_value > 0:
        atr_pips = atr / pip_value
        sl_floor_pips = atr_pips * _sl_floor_mult
        if sl_pips < sl_floor_pips:
            old_sl = sl_pips
            sl_pips = round(sl_floor_pips, 1)
            logger.info(
                "SL floor applied: %s %.1f → %.1f pips (2.5× ATR=%.1f)",
                symbol, old_sl, sl_pips, atr_pips,
            )

    # 6b. Enforce the regime's reward:risk floor (e.g. 2.0 in breakouts) so we
    # only take trades whose target pays for the risk.
    if sl_pips > 0 and (tp_pips / sl_pips) < policy.min_rr:
        tp_pips = round(sl_pips * policy.min_rr, 1)

    # 7. Get current price for entry
    close_price = float(features.get("close", 0))
    if close_price <= 0:
        return

    # ── Fix #6: Entry timing filter — reject "late to the party" entries ─
    # If price already moved >2× ATR in the signal direction during this
    # bar/session, the move has likely exhausted and we'd enter at the
    # worst possible level (selling at support, buying at resistance).
    _entry_disp_mult = await _get_runtime_setting("entry_displacement_atr_mult", 2.0, symbol)
    if atr > 0:
        bar_open = float(features.get("open", 0) or 0)
        if bar_open > 0:
            displacement = close_price - bar_open
            _max_displacement = atr * _entry_disp_mult
            if consensus.direction == "SELL" and displacement < -_max_displacement:
                logger.info(
                    "Entry timing REJECT %s: SELL but price already fell %.2f (> 2×ATR %.2f)",
                    symbol, abs(displacement), atr,
                )
                return
            if consensus.direction == "BUY" and displacement > _max_displacement:
                logger.info(
                    "Entry timing REJECT %s: BUY but price already rose %.2f (> 2×ATR %.2f)",
                    symbol, displacement, atr,
                )
                return

    # Calculate SL/TP prices
    if consensus.direction == "BUY":
        sl_price = close_price - (sl_pips * pip_value)
        tp_price = close_price + (tp_pips * pip_value)
    else:
        sl_price = close_price + (sl_pips * pip_value)
        tp_price = close_price - (tp_pips * pip_value)

    _autonomous_stats["last_signal"] = {
        "symbol": symbol,
        "direction": consensus.direction,
        "confidence": round(consensus.confidence, 3),
        "sl_pips": sl_pips,
        "tp_pips": tp_pips,
        "session": session.value,
        "mode": cfg.mode.value,
        "vol_regime": vol_regime,
        "source": "autonomous_ml",
        "timestamp": datetime.now(tz.utc).isoformat(),
    }

    # 8. Send to risk service for evaluation
    proposal = {
        "symbol": symbol,
        "direction": consensus.direction,
        "entry": close_price,
        "sl": round(sl_price, 6),
        "tp1": round(tp_price, 6),
        "conviction": consensus.confidence,
        "reasoning": f"[Autonomous ML] {consensus.reasoning} | SL={sl_pips} TP={tp_pips} [{vol_regime}]",
    }

    _autonomous_stats["trades_proposed"] += 1

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{risk_url}/api/risk/evaluate",
                json={"proposal": proposal, "state": {}},
            )
            if resp.status_code == 200:
                result = resp.json()
                decision = result.get("decision", "REJECT")

                if decision == "APPROVE":
                    _autonomous_stats["trades_approved"] += 1
                    approved_size = float(result.get("approved_size", 0.01))
                    # Scale size by the regime policy (smaller in ranges/unknown).
                    approved_size = max(0.01, round(approved_size * policy.size_mult, 2))
                    # Track direction for imbalance detection (Fix #7)
                    _record_direction(consensus.direction)
                    logger.info(
                        "Autonomous ML APPROVED: %s %s conf=%.3f SL=%.1f TP=%.1f lots=%.2f [%s|%s|%s]",
                        symbol, consensus.direction, consensus.confidence,
                        sl_pips, tp_pips, approved_size, cfg.mode.value, session.value, trend_regime,
                    )

                    # Log to autonomous signal stream (for dashboard tracking)
                    from shared.constants import RedisStream, STREAM_MAX_LEN
                    r = await _get_redis()
                    await r.xadd(
                        RedisStream.SIGNAL_AUTONOMOUS,
                        {
                            "symbol": symbol,
                            "direction": consensus.direction,
                            "confidence": str(consensus.confidence),
                            "entry": str(close_price),
                            "sl": str(sl_price),
                            "tp1": str(tp_price),
                            "sl_pips": str(sl_pips),
                            "tp_pips": str(tp_pips),
                            "source": "autonomous_ml",
                            "session": session.value,
                            "mode": cfg.mode.value,
                            "approved_size": str(approved_size),
                            "timestamp": datetime.now(tz.utc).isoformat(),
                        },
                        maxlen=STREAM_MAX_LEN,
                    )

                    try:
                        from shared.orchestrator import claim_active_signal
                        await claim_active_signal(
                            r, symbol, "autonomous_ml",
                            {
                                "direction": consensus.direction,
                                "confidence": consensus.confidence,
                            },
                        )
                    except Exception:
                        pass

                    # Send order directly to executor service for actual execution
                    try:
                        order_payload = {
                            "symbol": symbol,
                            "side": consensus.direction,
                            "order_type": "MARKET",
                            "size": approved_size,
                            "sl": round(sl_price, 5),
                            "tp": round(tp_price, 5),
                            "comment": build_nexus_comment("automl", tag=cfg.mode.value),
                            "strategy": "automl",
                            "conviction": round(consensus.confidence, 3),
                            "session": session.value,
                            "atr": round(float(atr or 0), 6),
                            "regime": str(trend_regime or ""),
                        }
                        exec_resp = await client.post(
                            f"{executor_url}/api/executor/order",
                            json=order_payload,
                        )
                        if exec_resp.status_code == 200:
                            exec_result = exec_resp.json()
                            logger.info(
                                "Autonomous ML EXECUTED: %s %s order_id=%s fill=%.5f",
                                symbol, consensus.direction,
                                exec_result.get("order_id", ""),
                                exec_result.get("fill_price", 0),
                            )
                        else:
                            logger.warning(
                                "Autonomous ML execution failed: %d %s",
                                exec_resp.status_code, exec_resp.text[:200],
                            )
                    except Exception:
                        logger.error("Autonomous ML executor call failed", exc_info=True)
                else:
                    _autonomous_stats["trades_rejected"] += 1
                    logger.info(
                        "Autonomous ML REJECTED: %s %s — %s",
                        symbol, consensus.direction,
                        result.get("violations", []),
                    )
            else:
                logger.warning("Risk service returned %d", resp.status_code)
    except httpx.ConnectError:
        logger.debug("Risk service unreachable for autonomous pipeline")
    except Exception:
        logger.error("Autonomous risk evaluation failed", exc_info=True)
        _autonomous_stats["errors"] += 1


# ── Internal helpers ─────────────────────────────────────────────────────────


async def _auto_retrain_callback() -> None:
    """Callback for the auto-retrain scheduler."""
    cfg = get_current_config()
    symbols = os.getenv("STREAM_SYMBOLS", "XAUUSD").split(",")
    await _run_training(["ml", "dl", "rl"], symbols, [cfg.operational_tf])


def _get_trader_by_type(model_type: str):
    if model_type == "ml":
        return _get_ml_trader()
    elif model_type == "dl":
        return _get_dl_trader()
    elif model_type == "rl":
        return _get_rl_trader()
    else:
        raise HTTPException(400, f"Unknown model type: {model_type}. Use 'ml', 'dl', or 'rl'.")


def _signal_to_response(sig: TradeSignal) -> TradeSignalResponse:
    return TradeSignalResponse(
        model_type=sig.model_type,
        model_name=sig.model_name,
        symbol=sig.symbol,
        direction=sig.direction,
        confidence=sig.confidence,
        sl_pips=sig.sl_pips,
        tp_pips=sig.tp_pips,
        regime=sig.regime,
        reasoning=sig.reasoning,
        timestamp=sig.timestamp,
    )


def _model_perf_score(perf: Any) -> float:
    """Turn a model's backtest performance into a non-negative skill score.

    Combines profit factor (vs 0.8 breakeven-ish baseline), win rate and a
    small Sharpe bonus. Unfitted/empty models score 0 so they don't earn weight.
    """
    try:
        if perf is None or not getattr(perf, "is_fitted", False):
            return 0.0
        pf = float(getattr(perf, "profit_factor", 0.0) or 0.0)
        wr = float(getattr(perf, "win_rate", 0.0) or 0.0)
        sharpe = float(getattr(perf, "sharpe_ratio", 0.0) or 0.0)
        score = max(0.0, pf - 0.8) + wr + max(0.0, sharpe) * 0.2
        return max(0.0, score)
    except Exception:
        return 0.0


def _dynamic_ensemble_weights(cfg: TradingModeConfig) -> dict[str, float]:
    """Blend the static mode weights with live performance-based weights.

    Each model earns weight proportional to its recent skill score; we blend
    50/50 with the configured base weights so a single great/bad backtest can't
    swing the ensemble wildly. Falls back to the static weights if no model has
    a usable track record yet (e.g. right after a cold start).
    """
    base = dict(cfg.ensemble_weights)
    try:
        scores = {
            "ml": _model_perf_score(_get_ml_trader().performance),
            "dl": _model_perf_score(_get_dl_trader().performance),
            "rl": _model_perf_score(_get_rl_trader().performance),
        }
        total = sum(scores.values())
        if total <= 0:
            return base
        perf_w = {k: scores[k] / total for k in scores}
        blended = {k: 0.5 * base.get(k, 0.33) + 0.5 * perf_w.get(k, 0.0) for k in ("ml", "dl", "rl")}
        s = sum(blended.values()) or 1.0
        weights = {k: blended[k] / s for k in blended}
        _autonomous_stats["dynamic_weights"] = {k: round(v, 3) for k, v in weights.items()}
        return weights
    except Exception:
        return base


def _compute_consensus(
    ml_sig: TradeSignal,
    dl_sig: TradeSignal,
    rl_sig: TradeSignal,
    symbol: str,
) -> TradeSignal:
    """Weighted consensus across all models."""
    # Mode-aware ensemble weights, dynamically adjusted by live performance.
    cfg = get_current_config()
    weights = _dynamic_ensemble_weights(cfg)  # performance-weighted ML/DL/RL
    signals = [
        (ml_sig, weights.get("ml", 0.34)),
        (dl_sig, weights.get("dl", 0.33)),
        (rl_sig, weights.get("rl", 0.33)),
    ]

    buy_score = sum(w * s.confidence for s, w in signals if s.direction == "BUY")
    sell_score = sum(w * s.confidence for s, w in signals if s.direction == "SELL")

    # A model that abstains (NO_TRADE) should NOT dilute the consensus the same
    # way a model voting against would. Previously the denominator was the sum of
    # ALL weights, so a perpetually-abstaining model (e.g. DL returning NO_TRADE)
    # capped conviction below the gate even when the models that DID vote agreed
    # strongly. We now normalise only over the weight of models that actually
    # cast a directional vote. A single voter with high conviction (>=0.65) is
    # allowed through with a 15% confidence penalty to support bootstrapping
    # scenarios where not all models are trained yet.
    voting = [(s, w) for s, w in signals if s.direction in ("BUY", "SELL")]
    num_voting = len(voting)
    voting_weight = sum(w for _, w in voting)
    # Denominator: weight of voting models only (fallback to full weight if none
    # voted, purely to avoid div-by-zero — that path is always NO_TRADE anyway).
    total_weight = voting_weight if voting_weight > 0 else sum(w for _, w in signals)

    if num_voting >= 2:
        has_consensus = True
    elif num_voting == 1:
        best_score = max(buy_score, sell_score)
        single_model_threshold = 0.75  # raised: single voter must be very confident
        if best_score / total_weight >= single_model_threshold:
            has_consensus = True
            logger.info("Single-model consensus allowed: score=%.3f >= %.3f", best_score / total_weight, single_model_threshold)
        else:
            has_consensus = False
    else:
        has_consensus = False

    # Mode-aware conviction threshold from session
    from datetime import datetime, timezone as tz
    session = detect_session(datetime.now(tz.utc).hour)
    sess_cfgs = SESSION_CONFIGS.get(cfg.mode, {})
    sess_cfg = sess_cfgs.get(session)
    min_conviction = sess_cfg.min_conviction if sess_cfg else 0.50

    # Front-configurable conviction override (None = keep per-session default).
    if runtime_settings is not None:
        try:
            override = runtime_settings.get_cached().get("min_conviction_override")
            if override is not None:
                min_conviction = float(override)
        except Exception:
            pass

    if has_consensus and buy_score > sell_score and buy_score / total_weight > min_conviction:
        direction = "BUY"
        confidence = buy_score / total_weight
        if num_voting == 1:
            confidence *= 0.70  # larger haircut: single-voter trades are risky
    elif has_consensus and sell_score > buy_score and sell_score / total_weight > min_conviction:
        direction = "SELL"
        confidence = sell_score / total_weight
        if num_voting == 1:
            confidence *= 0.70
    else:
        direction = "NO_TRADE"
        confidence = 0.0
        # Explain *why* there is no entry so the console is actionable instead
        # of silent. Shows the weighted scores vs the conviction threshold, how
        # many models voted, and what each model voted.
        best_side = "BUY" if buy_score >= sell_score else "SELL"
        best_conv = (max(buy_score, sell_score) / total_weight) if total_weight else 0.0
        reason = "only %d model voted" % num_voting if not has_consensus else "conv<min"
        logger.info(
            "NO_TRADE %s [%s|%s]: buy=%.3f sell=%.3f best=%s conv=%.3f < min=%.3f "
            "voters=%d (%s) (ML=%s/%.2f DL=%s/%.2f RL=%s/%.2f)",
            symbol, cfg.mode.value, session.value,
            buy_score / total_weight if total_weight else 0.0,
            sell_score / total_weight if total_weight else 0.0,
            best_side, best_conv, min_conviction, num_voting, reason,
            ml_sig.direction, ml_sig.confidence,
            dl_sig.direction, dl_sig.confidence,
            rl_sig.direction, rl_sig.confidence,
        )

    # Average SL/TP from agreeing models, with mode-aware defaults
    agreeing = [s for s, _ in signals if s.direction == direction and direction != "NO_TRADE"]
    sl_default = (cfg.sl_min + cfg.sl_max) / 2
    tp_default = (cfg.tp_min + cfg.tp_max) / 2
    sl_pips = float(np.mean([s.sl_pips for s in agreeing])) if agreeing else sl_default
    tp_pips = float(np.mean([s.tp_pips for s in agreeing])) if agreeing else tp_default

    return TradeSignal(
        model_type="consensus",
        model_name="Weighted_Consensus",
        symbol=symbol,
        direction=direction,
        confidence=confidence,
        sl_pips=sl_pips,
        tp_pips=tp_pips,
        session=session.value,
        reasoning=f"ML={ml_sig.direction}({ml_sig.confidence:.2f}) "
                  f"DL={dl_sig.direction}({dl_sig.confidence:.2f}) "
                  f"RL={rl_sig.direction}({rl_sig.confidence:.2f}) "
                  f"[{cfg.mode.value}|{session.value}|conv≥{min_conviction}]",
    )


async def _get_htf_trends(symbol: str, timeframes: list[str]) -> dict[str, str]:
    """Fetch higher-timeframe trend directions from Redis feature cache.

    Returns e.g. {"M15": "BULLISH", "H1": "BEARISH"}.
    """
    trends: dict[str, str] = {}
    try:
        r = await _get_redis()
        for tf in timeframes:
            cache_key = f"nexus:features:{symbol}:{tf}"
            raw = await r.get(cache_key)
            if raw is not None:
                data = json.loads(raw)
                feats = data.get("features", data) if isinstance(data, dict) else {}
                # Determine trend from EMA cross or MACD
                ema_fast = feats.get("ema_9", feats.get("ema_20", 0))
                ema_slow = feats.get("ema_50", feats.get("ema_200", 0))
                macd_hist = feats.get("macd_histogram", feats.get("macd_hist", 0))
                if ema_fast > ema_slow and macd_hist > 0:
                    trends[tf] = "BULLISH"
                elif ema_fast < ema_slow and macd_hist < 0:
                    trends[tf] = "BEARISH"
                else:
                    trends[tf] = "NEUTRAL"
            else:
                trends[tf] = "NEUTRAL"
    except Exception:
        logger.debug("Failed to fetch HTF trends", exc_info=True)
        for tf in timeframes:
            trends.setdefault(tf, "NEUTRAL")
    return trends


def _detect_volatility_regime(features: dict[str, float]) -> str:
    """Classify current volatility regime from feature data."""
    atr = features.get("atr_14", features.get("atr", 0.0))
    atr_sma = features.get("atr_sma", features.get("atr_20_sma", atr))
    if atr_sma <= 0:
        return "MEDIUM"
    ratio = atr / atr_sma if atr_sma > 0 else 1.0
    if ratio > 1.8:
        return "EXTREME"
    elif ratio > 1.3:
        return "HIGH"
    elif ratio < 0.6:
        return "LOW"
    return "MEDIUM"


def _get_genai_performance() -> dict[str, Any]:
    """Build GenAI performance stats from trade memory (genai_agents source)."""
    base = {
        "model_type": "genai",
        "model_name": "GenAI Agents",
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "sharpe_ratio": 0.0,
        "max_drawdown": 0.0,
        "total_return": 0.0,
        "total_trades": 0,
        "avg_rr": 0.0,
        "is_fitted": True,
        "last_trained": "",
        "equity_curve": [],
    }
    try:
        memory = get_trade_memory()
        trades = [
            t for t in memory._trades
            if str(getattr(t, "source", "")).startswith("genai")
            or "genai" in str(getattr(t, "source", "")).lower()
        ]
        if not trades:
            return base

        wins = [t for t in trades if t.is_win or t.pnl_usd > 0]
        losses = [t for t in trades if not (t.is_win or t.pnl_usd > 0)]
        total = len(trades)
        gross_win = sum(max(0.0, t.pnl_usd) for t in trades)
        gross_loss = abs(sum(min(0.0, t.pnl_usd) for t in trades))
        pf = gross_win / gross_loss if gross_loss > 0 else (gross_win if gross_win > 0 else 0.0)
        equity = 0.0
        curve: list[float] = []
        for t in trades:
            equity += t.pnl_usd
            curve.append(round(equity, 2))
        peak = 0.0
        max_dd = 0.0
        for v in curve:
            peak = max(peak, v)
            if peak > 0:
                max_dd = max(max_dd, (peak - v) / peak * 100)

        base.update({
            "win_rate": len(wins) / total if total else 0.0,
            "profit_factor": round(pf, 2),
            "total_trades": total,
            "total_return": round(equity, 2),
            "max_drawdown": round(max_dd, 2),
            "equity_curve": curve[-50:],
            "avg_rr": round(
                sum(t.pnl_pips for t in wins) / max(len(wins), 1)
                / max(abs(sum(t.pnl_pips for t in losses) / max(len(losses), 1)), 0.1),
                2,
            ) if wins and losses else 0.0,
        })
    except Exception:
        logger.debug("GenAI performance unavailable", exc_info=True)
    return base


async def _get_account_balance() -> float:
    """Fetch real account balance from Redis cache or executor."""
    try:
        r = await _get_redis()
        cached = await r.get("nexus:risk:state")
        if cached:
            state = json.loads(cached)
            bal = float(state.get("balance", 0))
            if bal > 0:
                return bal
    except Exception:
        pass
    try:
        import httpx
        executor_url = os.getenv("NEXUS_EXECUTOR_URL", "http://localhost:8007")
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{executor_url}/api/executor/positions")
            if resp.status_code == 200:
                data = resp.json()
                bal = float(data.get("account", {}).get("balance", 0))
                if bal > 0:
                    return bal
    except Exception:
        pass
    return 10000.0


async def _get_latest_features(
    symbol: str,
    operational_tf: str | None = None,
) -> dict[str, float] | None:
    """Fetch latest features from Redis.

    Searches operational TF first (dynamic or mode default) to minimise
    decision latency for scalping.
    """
    try:
        r = await _get_redis()
        cfg = get_current_config()
        if operational_tf is None:
            cached = await _read_dynamic_tf()
            operational_tf = (cached or {}).get("operational_tf")
        primary = operational_tf or cfg.operational_tf
        search_order = [primary] + [
            tf for tf in ("M1", "M5", "M15", "H1") if tf != primary
        ]
        for tf in search_order:
            cache_key = f"nexus:features:{symbol}:{tf}"
            raw = await r.get(cache_key)
            if raw is not None:
                data = json.loads(raw)
                if isinstance(data, dict) and "features" in data:
                    return data["features"]
                return data
    except Exception:
        logger.debug("Failed to get features from Redis", exc_info=True)
    return None


async def _fetch_training_data(
    symbols: list[str],
    timeframes: list[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]] | None:
    """Fetch historical data for training from Redis.

    Combines three sources with different weights:
    - nexus:features:history:{symbol}  — M5 bar features (1×)
    - recency boost: last 500 history records are duplicated (2× effective)
    - nexus:features:outcomes:{symbol} — features captured at live trade entry,
      repeated 5× so models learn from actual TP/SL results, not just direction.
    """
    try:
        r = await _get_redis()

        all_features: list[list[float]] = []
        all_labels: list[int] = []
        all_ohlcv: list[list[float]] = []
        feature_names: list[str] = []

        def _parse_record(
            raw: str,
            feature_names_ref: list[str],
            repeat: int = 1,
        ) -> tuple[list[list[float]], list[int], list[list[float]], list[str]]:
            feats_out: list[list[float]] = []
            labels_out: list[int] = []
            ohlcv_out: list[list[float]] = []
            try:
                record = json.loads(raw)
                feats = record.get("features", {})
                label = record.get("label")
                ohlcv_d = record.get("ohlcv", {})

                if not feats or label is None:
                    return feats_out, labels_out, ohlcv_out, feature_names_ref

                if not feature_names_ref:
                    feature_names_ref = [k for k in feats.keys() if isinstance(feats[k], (int, float))]

                row = [float(feats.get(k, 0.0)) for k in feature_names_ref]
                o = float(ohlcv_d.get("open", feats.get("open", 0)))
                h = float(ohlcv_d.get("high", feats.get("high", 0)))
                lv = float(ohlcv_d.get("low", feats.get("low", 0)))
                c = float(ohlcv_d.get("close", feats.get("close", 0)))
                v = float(ohlcv_d.get("volume", feats.get("volume", 0)))
                for _ in range(repeat):
                    feats_out.append(row)
                    labels_out.append(int(label))
                    ohlcv_out.append([o, h, lv, c, v])
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
            return feats_out, labels_out, ohlcv_out, feature_names_ref

        for symbol in symbols:
            # ── Historical bar features ──────────────────────────────────
            history_key = f"nexus:features:history:{symbol}"
            raw_list = await r.lrange(history_key, 0, -1)
            total = len(raw_list)
            recency_start = max(0, total - 500)

            for idx, raw in enumerate(raw_list):
                repeat = 2 if idx >= recency_start else 1
                f, lb, ov, feature_names = _parse_record(raw, feature_names, repeat)
                all_features.extend(f)
                all_labels.extend(lb)
                all_ohlcv.extend(ov)

            # ── Outcome-labelled records from live trades (5× weight) ────
            # Normalize to canonical symbol so GOLD trades match XAUUSD outcomes
            try:
                from shared.asset_specs import get_asset_spec as _gspec
                _canonical_sym = _gspec(symbol).symbol
            except Exception:
                _canonical_sym = symbol.upper()
            outcomes_key = f"nexus:features:outcomes:{_canonical_sym}"
            outcome_list = await r.lrange(outcomes_key, 0, -1)
            for raw in outcome_list:
                f, lb, ov, feature_names = _parse_record(raw, feature_names, repeat=5)
                all_features.extend(f)
                all_labels.extend(lb)
                all_ohlcv.extend(ov)

            if outcome_list:
                logger.info(
                    "Training data: %d outcome records (×5) loaded for %s",
                    len(outcome_list), symbol,
                )

        if not all_features:
            return None

        X = np.array(all_features, dtype=np.float64)
        y = np.array(all_labels, dtype=np.int64)
        ohlcv = np.array(all_ohlcv, dtype=np.float64)

        return X, y, ohlcv, feature_names

    except Exception:
        logger.error("Failed to fetch training data", exc_info=True)
        return None


async def _run_training(
    model_types: list[str],
    symbols: list[str],
    timeframes: list[str],
) -> None:
    """Background task: train selected model types."""
    try:
        data = await _fetch_training_data(symbols, timeframes)
        if data is None:
            allow_synthetic = os.getenv("NEXUS_ALLOW_SYNTHETIC_TRAINING", "0").lower() in ("1", "true")
            if allow_synthetic:
                logger.warning("No MT5 training data — using synthetic (dev only)")
                data = _generate_synthetic_data()
            else:
                logger.error(
                    "No real MT5 training data for traders — train skipped. "
                    "Ensure nexus-data is streaming OHLCV from MetaTrader 5."
                )
                for mt in model_types:
                    _training_status[mt] = False
                return

        X, y, ohlcv, feature_names = data

        # Pass default symbol so pip_value is correctly inferred during training
        default_symbol = symbols[0] if symbols else os.getenv("STREAM_SYMBOLS", "XAUUSD").split(",")[0]

        for model_type in model_types:
            try:
                logger.info("Training %s trader on %d samples...", model_type, len(X))
                trader = _get_trader_by_type(model_type)
                if trader is None or model_type not in ("ml", "dl", "rl"):
                    continue

                # Offload the CPU-bound train+backtest to a worker thread so the
                # asyncio event loop stays responsive. Previously this ran inline
                # and froze nexus-ml for ~5 min, making the gateway proxy (and
                # therefore the ML/DL/RL/Config tabs) time out.
                metrics = await asyncio.to_thread(
                    _train_and_backtest_sync,
                    trader, model_type, X, y, ohlcv, feature_names, default_symbol,
                )
                logger.info("Trader %s training complete: %s", model_type, metrics)

            except Exception:
                logger.error("Training failed for %s", model_type, exc_info=True)
            finally:
                _training_status[model_type] = False

    except Exception:
        logger.error("Training pipeline failed", exc_info=True)
        for mt in model_types:
            _training_status[mt] = False


def _train_and_backtest_sync(
    trader,
    model_type: str,
    X: np.ndarray,
    y: np.ndarray,
    ohlcv: np.ndarray,
    feature_names: list[str],
    default_symbol: str,
) -> dict:
    """Synchronous train + save + backtest for one trader.

    Runs inside ``asyncio.to_thread`` so it never blocks the event loop.
    """
    # Classifiers (ML/DL) need at least 2 label classes. In very flat markets a
    # training window can end up single-class, which makes XGBoost raise
    # ("Invalid classes inferred from unique values of y"). Skip gracefully and
    # keep the previously fitted model instead of erroring the whole retrain.
    if model_type in ("ml", "dl"):
        try:
            n_classes = len(np.unique(y[~np.isnan(y)])) if y is not None else 0
        except Exception:
            n_classes = len(set(y.tolist())) if y is not None else 0
        if n_classes < 2:
            logger.warning(
                "Skip %s training: only %d label class in window (need >=2)",
                model_type, n_classes,
            )
            return {"skipped": "single_class", "n_classes": n_classes}

    if model_type == "ml":
        metrics = trader.train(X, y, ohlcv=ohlcv, feature_names=feature_names, symbol=default_symbol)
    elif model_type == "dl":
        metrics = trader.train(X, y, ohlcv=ohlcv, feature_names=feature_names, epochs=30, symbol=default_symbol)
    elif model_type == "rl":
        metrics = trader.train(X, y, ohlcv=ohlcv, feature_names=feature_names, n_epochs=15, symbol=default_symbol)
    else:
        return {}

    trader.save()
    _run_backtest_for_trader(trader, X, y, ohlcv, feature_names)
    return metrics


def _run_backtest_for_trader(
    trader,
    X: np.ndarray,
    y: np.ndarray,
    ohlcv: np.ndarray,
    feature_names: list[str],
) -> None:
    """Run SL/TP backtest and update trader performance."""
    try:
        cfg = get_current_config()
        n = len(X)
        signals = np.zeros(n, dtype=np.int32)
        sl_default = (cfg.sl_min + cfg.sl_max) / 2
        tp_default = (cfg.tp_min + cfg.tp_max) / 2
        sl_arr = np.full(n, sl_default)
        tp_arr = np.full(n, tp_default)

        for i in range(n):
            sig = trader.predict(X[i], "BACKTEST")
            if sig.direction == "BUY":
                signals[i] = 1
            elif sig.direction == "SELL":
                signals[i] = -1
            sl_arr[i] = sig.sl_pips
            tp_arr[i] = sig.tp_pips

        # Mode-aware backtester parameters
        bars_per_year = 252 * 24 * 12 if cfg.operational_tf == "M5" else 252 * 24 * 4
        bt = SLTPBacktester(
            spread_pips=cfg.bt_spread_pips,
            risk_per_trade=cfg.risk_per_trade,
            max_hold_bars=cfg.max_hold_bars,
            trailing_trigger_rr=cfg.bt_trailing_trigger_rr,
            trailing_offset_pips=cfg.bt_trailing_offset_pips,
            bars_per_year=bars_per_year,
        )
        result = bt.backtest(signals, sl_arr, tp_arr, ohlcv)

        perf = trader._performance
        perf.total_trades = result.total_trades
        perf.win_rate = result.win_rate
        perf.profit_factor = result.profit_factor
        perf.sharpe_ratio = result.sharpe_ratio
        perf.sortino_ratio = result.sortino_ratio
        perf.max_drawdown = result.max_drawdown
        perf.total_return = result.total_return
        perf.avg_rr = result.avg_rr
        perf.avg_win_pips = result.avg_win_pips
        perf.avg_loss_pips = result.avg_loss_pips
        perf.expectancy = result.expectancy
        perf.equity_curve = result.equity_curve[-200:]

        logger.info(
            "Backtest for %s: WR=%.2f PF=%.2f Sharpe=%.2f",
            trader.model_name, result.win_rate, result.profit_factor, result.sharpe_ratio,
        )

    except Exception:
        logger.error("Backtest failed for %s", trader.model_name, exc_info=True)


def _generate_synthetic_data(
    n: int = 5000,
    n_features: int = 30,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """Generate synthetic trading data for testing when no real data is available."""
    rng = np.random.default_rng(42)

    feature_names = [
        "rsi", "macd", "macd_signal", "macd_histogram",
        "ema_9", "ema_20", "ema_50", "ema_200",
        "bb_upper", "bb_middle", "bb_lower",
        "atr", "adx", "di_plus", "di_minus",
        "stoch_k", "stoch_d", "cci", "mfi", "obv",
        "volume_sma", "close", "open", "high", "low",
        "returns_1", "returns_5", "volatility_10",
        "momentum_10", "roc_10",
    ][:n_features]

    X = rng.standard_normal((n, n_features)).astype(np.float64)

    # Synthetic price series
    price = 2000.0 + np.cumsum(rng.standard_normal(n) * 0.5)
    ohlcv = np.column_stack([
        price - rng.uniform(0, 1, n),   # open
        price + rng.uniform(0, 2, n),   # high
        price - rng.uniform(0, 2, n),   # low
        price,                           # close
        rng.uniform(100, 1000, n),      # volume
    ])

    # Labels: 1 if next close > current close
    y = np.zeros(n, dtype=np.int64)
    y[:-1] = (price[1:] > price[:-1]).astype(np.int64)

    return X, y, ohlcv, feature_names


# ── RL Lifecycle Manager ─────────────────────────────────────────────────────
# Monitors open positions every 30s and writes recommended actions to Redis.

_lifecycle_agent: RLLifecycleAgent | None = None
_lifecycle_task: asyncio.Task | None = None
_LIFECYCLE_KEY_PREFIX = "nexus:rl:lifecycle:"
_LIFECYCLE_ALL_KEY = "nexus:rl:lifecycle:all"


def _get_lifecycle_agent() -> RLLifecycleAgent:
    global _lifecycle_agent
    if _lifecycle_agent is None:
        _lifecycle_agent = RLLifecycleAgent(model_dir=TRADERS_DIR)
        _lifecycle_agent.load()
    return _lifecycle_agent


async def _build_lifecycle_episodes_from_memory() -> list[dict]:
    """Convert TradeMemory records into lifecycle training episodes."""
    try:
        mem = get_trade_memory()
        records = mem._records  # list[TradeRecord]
        episodes = []
        r = await _get_redis()
        for rec in records:
            if not rec.closed_at or not rec.entry_price or not rec.exit_price:
                continue
            # Fetch bar context from Redis (best-effort)
            tf = "M5"
            raw_bars = await r.lrange(f"nexus:ohlcv:{rec.symbol}:{tf}", -20, -1)
            bars: list[dict] = []
            for rb in raw_bars:
                try:
                    b = json.loads(rb)
                    bars.append({
                        "close": float(b.get("close", rec.entry_price)),
                        "atr": float(b.get("atr", 5.0)),
                        "rsi": float(b.get("rsi", 50.0)),
                        "adx": float(b.get("adx", 25.0)),
                        "macd_hist": float(b.get("macd_histogram", 0.0)),
                        "spread": float(b.get("spread_pips", 1.5)),
                        "mtf_score": 0.0,
                    })
                except Exception:
                    continue
            if not bars:
                # Synthetic 1-bar fallback
                bars = [{"close": float(rec.exit_price), "atr": 5.0,
                         "rsi": 50.0, "adx": 25.0, "macd_hist": 0.0,
                         "spread": 1.5, "mtf_score": 0.0}]

            pnl = float(rec.pnl_pips or 0.0)
            episodes.append({
                "side": rec.side or "BUY",
                "entry_price": float(rec.entry_price),
                "sl_price": float(rec.entry_price) * (0.99 if rec.side == "BUY" else 1.01),
                "tp_price": float(rec.entry_price) * (1.02 if rec.side == "BUY" else 0.98),
                "final_pnl_pips": pnl,
                "regime": rec.regime or "RANGING",
                "quality_score": 60.0,
                "open_time": str(rec.opened_at or ""),
                "bars": bars,
            })
        return episodes
    except Exception as exc:
        logger.warning("lifecycle episode build failed: %s", exc)
        return []


async def _retrain_lifecycle_if_enough_data() -> None:
    """Retrain lifecycle model when enough TradeMemory records are available."""
    try:
        mem = get_trade_memory()
        if len(mem._records) < 10:
            logger.info("lifecycle retrain skipped — only %d records", len(mem._records))
            return
        episodes = await _build_lifecycle_episodes_from_memory()
        if not episodes:
            return
        agent = _get_lifecycle_agent()
        metrics = agent.train(episodes, n_epochs=15, steps_per_epoch=512)
        agent.save()
        logger.info("lifecycle retrained on %d episodes: %s", len(episodes), metrics)
    except Exception:
        logger.error("lifecycle retrain failed", exc_info=True)


async def _lifecycle_monitor() -> None:
    """Background task: evaluates all open Nexus positions every 30s."""
    import httpx
    executor_url = os.getenv("NEXUS_EXECUTOR_URL", "http://localhost:8007")
    while True:
        try:
            await asyncio.sleep(30)
            agent = _get_lifecycle_agent()
            r = await _get_redis()

            # Fetch open positions from executor
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{executor_url}/api/executor/positions")
            if resp.status_code != 200:
                continue

            data = resp.json()
            nexus_positions = data.get("nexus_positions", []) or data.get("positions", [])
            if not nexus_positions:
                await r.delete(_LIFECYCLE_ALL_KEY)
                continue

            # Fetch current regime and MTF context
            regime_raw = await r.get(_REGIME_KEY)
            regime_info = json.loads(regime_raw) if regime_raw else {}
            current_regime = regime_info.get("regime", "RANGING")

            all_actions: list[dict] = []
            cfg = get_current_config()

            for pos in nexus_positions:
                try:
                    ticket = int(pos.get("ticket") or pos.get("id") or 0)
                    symbol = str(pos.get("symbol", "XAUUSD"))
                    side = str(pos.get("side") or pos.get("type", "BUY")).upper()
                    entry_p = float(pos.get("entry_price") or pos.get("open_price") or 0.0)
                    current_p = float(pos.get("current_price") or pos.get("price") or entry_p)
                    sl_p = float(pos.get("sl") or pos.get("stop_loss") or 0.0)
                    tp_p = float(pos.get("tp") or pos.get("take_profit") or 0.0)
                    lot = float(pos.get("volume") or pos.get("lot_size") or 0.01)

                    # Estimate bars_held from open_time
                    open_time_str = str(pos.get("open_time") or pos.get("time") or "")
                    bars_held = 1
                    if open_time_str:
                        try:
                            from dateutil import parser as dtparser
                            open_dt = dtparser.parse(open_time_str)
                            if open_dt.tzinfo is None:
                                open_dt = open_dt.replace(tzinfo=timezone.utc)
                            elapsed_min = (datetime.now(timezone.utc) - open_dt).total_seconds() / 60
                            tf_min = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}.get(cfg.operational_tf, 5)
                            bars_held = max(1, int(elapsed_min / tf_min))
                        except Exception:
                            pass

                    # Get market features for this symbol
                    features = await _get_latest_features(symbol)
                    atr = float((features or {}).get("atr", 5.0))
                    rsi = float((features or {}).get("rsi", 50.0))
                    adx = float((features or {}).get("adx", 25.0))
                    macd_h = float((features or {}).get("macd_histogram", 0.0))
                    spread = float((features or {}).get("spread_pips", 1.5))

                    # Get MTF alignment from Redis
                    mtf_raw = await r.get(f"nexus:mtf:{symbol}")
                    mtf_score = 0.0
                    if mtf_raw:
                        try:
                            mtf_score = float(json.loads(mtf_raw).get("alignment_score", 0.0))
                        except Exception:
                            pass

                    ps = PositionState(
                        ticket=ticket, symbol=symbol, side=side,
                        entry_price=entry_p, current_price=current_p,
                        sl_price=sl_p if sl_p > 0 else (entry_p * 0.99 if side == "BUY" else entry_p * 1.01),
                        tp_price=tp_p if tp_p > 0 else (entry_p * 1.02 if side == "BUY" else entry_p * 0.98),
                        lot_size=lot,
                        open_time=open_time_str,
                        bars_held=bars_held,
                        atr=atr, regime=current_regime,
                        mtf_alignment_score=mtf_score,
                        spread_pips=spread,
                        signal_quality=60.0,
                        rsi=rsi, adx=adx, macd_hist=macd_h,
                        max_hold_bars=cfg.max_hold_bars,
                        pip_value=get_pip_value(symbol),
                    )

                    if agent._is_fitted:
                        action_label, probs = agent.decide(ps)
                    else:
                        action_label, probs = RLLifecycleAgent.rule_based_decide(ps)

                    action_data = {
                        "ticket": ticket, "symbol": symbol, "side": side,
                        "action": action_label,
                        "color": ACTION_COLORS.get(action_label, "neutral"),
                        "probs": probs,
                        "pnl_pips": round(ps.pnl_pips, 1),
                        "bars_held": bars_held,
                        "regime": current_regime,
                        "confidence": round(float(max(probs.values()) if probs else 0.0), 3),
                        "entry_price": entry_p,
                        "current_price": current_p,
                        "lot_size": lot,
                        "model": "ppo" if agent._is_fitted else "rule_based",
                        "evaluated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    all_actions.append(action_data)

                    # Per-ticket key for quick lookup
                    await r.set(
                        f"{_LIFECYCLE_KEY_PREFIX}{ticket}",
                        json.dumps(action_data),
                        ex=120,
                    )
                except Exception:
                    logger.debug("lifecycle: error evaluating position %s", pos, exc_info=True)

            # Write all actions as one Redis key for the dashboard
            await r.set(_LIFECYCLE_ALL_KEY, json.dumps(all_actions), ex=120)
            if all_actions:
                logger.debug("lifecycle: evaluated %d positions", len(all_actions))

        except asyncio.CancelledError:
            break
        except Exception:
            logger.warning("lifecycle monitor error", exc_info=True)
            await asyncio.sleep(10)


# ── Regime Publisher ─────────────────────────────────────────────────────────
# Publishes combined HMM + rule-based regime to Redis every cycle so the
# dashboard and other services always have the latest regime info.

_hmm_detector = None  # lazy-loaded singleton
_REGIME_KEY = "nexus:regime:current"
_REGIME_HISTORY_KEY = "nexus:regime:history"


def _get_hmm_detector():
    """Lazy-load the HMM detector from disk."""
    global _hmm_detector
    if _hmm_detector is not None:
        return _hmm_detector
    try:
        from .models.hmm_regime import HMMRegimeDetector
        hmm_path = Path(MODEL_DIR) / "hmm_regime.pkl"
        if hmm_path.exists():
            det = HMMRegimeDetector()
            det.load(hmm_path)
            _hmm_detector = det
            return det
    except Exception:
        logger.debug("Could not load HMM detector", exc_info=True)
    return None


async def _retrain_hmm_on_real_data() -> bool:
    """Retrain HMM on real H1 OHLCV from Redis (not synthetic).

    Returns True if training succeeded.
    """
    try:
        from .models.hmm_regime import HMMRegimeDetector
        r = await _get_redis()
        symbols = os.getenv("STREAM_SYMBOLS", "XAUUSD").split(",")
        all_bars: list[list[float]] = []
        for sym in symbols:
            for tf in ("H1", "M15", "M5"):
                raw = await r.get(f"nexus:ohlcv:{sym}:{tf}")
                if raw:
                    import json as _json
                    bars = _json.loads(raw)
                    if len(bars) >= 40:
                        for b in bars:
                            try:
                                all_bars.append([
                                    float(b.get("open", 0)),
                                    float(b.get("high", 0)),
                                    float(b.get("low", 0)),
                                    float(b.get("close", 0)),
                                    float(b.get("volume", 1)),
                                ])
                            except (TypeError, ValueError):
                                pass
                        break  # use highest available TF that has data
        if len(all_bars) < 40:
            return False
        import numpy as np
        ohlcv = np.array(all_bars, dtype=np.float64)
        det = HMMRegimeDetector()
        det.fit(ohlcv)
        hmm_path = Path(MODEL_DIR) / "hmm_regime.pkl"
        det.save(hmm_path)
        global _hmm_detector
        _hmm_detector = det
        logger.info("HMM retrained on %d real bars", len(all_bars))
        return True
    except Exception:
        logger.warning("HMM retrain on real data failed", exc_info=True)
        return False


async def _publish_regime(symbol: str, features: dict[str, Any]) -> dict[str, Any]:
    """Classify regime using HMM + rule-based, publish to Redis.

    Returns the regime dict so the caller can gate decisions on it.
    """
    from datetime import datetime, timezone as tz

    # Rule-based classification (always available)
    rule_regime = classify_trend_regime(features)

    # HMM classification (when model is fitted)
    hmm_regime = "UNKNOWN"
    hmm_confidence = 0.0
    hmm_state_probs: dict[str, float] = {}
    det = _get_hmm_detector()
    if det is not None and det.is_fitted:
        try:
            info = det.predict_regime_info(features)
            hmm_regime = info.regime
            hmm_confidence = info.confidence
            hmm_state_probs = info.state_probabilities
        except Exception:
            pass

    # Combined regime: HMM wins when confidence > 0.65, else rule-based.
    if hmm_confidence >= 0.65 and hmm_regime not in ("UNKNOWN", ""):
        # Map HMM 4-state names to our 4 regime labels
        regime_map = {
            "TRENDING_UP": "TRENDING_BULL",
            "TRENDING_DOWN": "TRENDING_BEAR",
            "RANGING": "RANGING",
            "HIGH_VOLATILITY": "HIGH_VOL",
        }
        final_regime = regime_map.get(hmm_regime, rule_regime)
        source = "hmm"
    else:
        final_regime = rule_regime
        source = "rule_based"

    # Transition detection: compare to previous regime.
    try:
        r = await _get_redis()
        prev_raw = await r.get(_REGIME_KEY)
        prev_regime = ""
        if prev_raw:
            prev_data = json.loads(prev_raw)
            prev_regime = prev_data.get("regime", "")
    except Exception:
        prev_regime = ""

    is_transition = bool(prev_regime and prev_regime != final_regime)

    payload = {
        "symbol": symbol,
        "regime": final_regime,
        "rule_regime": rule_regime,
        "hmm_regime": hmm_regime,
        "hmm_confidence": round(hmm_confidence, 4),
        "hmm_state_probs": hmm_state_probs,
        "source": source,
        "is_transition": is_transition,
        "previous_regime": prev_regime,
        "volatility_regime": _detect_volatility_regime(features),
        "updated_at": datetime.now(tz.utc).isoformat(),
    }

    try:
        r = await _get_redis()
        await r.set(_REGIME_KEY, json.dumps(payload), ex=300)
        # Keep last 20 regime transitions for dashboard history.
        if is_transition:
            await r.lpush(_REGIME_HISTORY_KEY, json.dumps({
                "from": prev_regime, "to": final_regime,
                "source": source, "at": payload["updated_at"],
            }))
            await r.ltrim(_REGIME_HISTORY_KEY, 0, 19)
    except Exception:
        logger.debug("Failed to publish regime", exc_info=True)

    return payload


# ── Signal Quality Gate ───────────────────────────────────────────────────────
# 8 dynamic filters scored 0-100. All thresholds derived from TradeMemory.

_QUALITY_KEY = "nexus:signal:quality:last"


async def _compute_signal_quality(
    symbol: str,
    features: dict[str, Any],
    direction: str,
    regime_info: dict[str, Any],
    htf_trends: dict[str, str],
    session_value: str,
    current_spread: float,
    avg_spread: float,
) -> tuple[int, dict[str, Any]]:
    """Compute a 0-100 quality score for the current signal.

    Returns (score, breakdown_dict).
    """
    from datetime import datetime, timezone as tz

    breakdown: dict[str, Any] = {}
    score = 0

    # Load dynamic thresholds (non-blocking — falls back to sensible defaults).
    try:
        mem = get_trade_memory()
        thresholds = await mem.get_dynamic_thresholds()
    except Exception:
        thresholds = {}

    hour_wr = thresholds.get("hour_wr", {})
    bad_hours = thresholds.get("bad_hours", [])
    regime_min_wr = thresholds.get("regime_min_wr", {})
    total_trades = thresholds.get("total_trades", 0)

    # ── Filter 1: Spread (15 pts) ──────────────────────────────────────────
    # Dynamic: spread < 75th-pct-of-avg is fine; absolute fallback 3 pips.
    spread_ok_pct = avg_spread * 1.5 if avg_spread > 0 else 3.0
    spread_ok_pct = max(spread_ok_pct, 0.5)  # floor
    if current_spread <= spread_ok_pct:
        score += 15
        breakdown["spread"] = {"ok": True, "pts": 15,
                               "detail": f"{current_spread:.2f} ≤ {spread_ok_pct:.2f}"}
    else:
        breakdown["spread"] = {"ok": False, "pts": 0,
                               "detail": f"{current_spread:.2f} > {spread_ok_pct:.2f}"}

    # ── Filter 2: HTF Confluence (20 pts) ─────────────────────────────────
    # Points proportional to how many HTFs agree with signal direction.
    htf_target = "BULLISH" if direction == "BUY" else "BEARISH"
    agree = sum(1 for v in htf_trends.values() if v == htf_target)
    total_htf = len(htf_trends)
    if total_htf > 0:
        htf_pts = round((agree / total_htf) * 20)
        score += htf_pts
        breakdown["htf_confluence"] = {
            "ok": agree > 0, "pts": htf_pts,
            "detail": f"{agree}/{total_htf} TFs agree ({','.join(htf_trends.keys())})",
        }
    else:
        breakdown["htf_confluence"] = {"ok": False, "pts": 0, "detail": "no HTF data"}

    # ── Filter 3: News safety (15 pts) ────────────────────────────────────
    try:
        r = await _get_redis()
        pause_until_raw = await r.get("nexus:news:pause_until")
        news_ok = True
        if pause_until_raw:
            from datetime import datetime as _dt
            pause_until = _dt.fromisoformat(pause_until_raw)
            if _dt.now(tz.utc) < pause_until.replace(tzinfo=tz.utc):
                news_ok = False
    except Exception:
        news_ok = True  # fail open — don't block trades on Redis error

    if news_ok:
        score += 15
        breakdown["news_safe"] = {"ok": True, "pts": 15, "detail": "no pending event"}
    else:
        breakdown["news_safe"] = {"ok": False, "pts": 0, "detail": "news pause active"}

    # ── Filter 4: Not late entry (15 pts) ─────────────────────────────────
    atr = float(features.get("atr_14", features.get("atr", 0)) or 0)
    bar_open = float(features.get("open", 0) or 0)
    close_px = float(features.get("close", 0) or 0)
    if atr > 0 and bar_open > 0 and close_px > 0:
        displacement = abs(close_px - bar_open)
        displacement_ratio = displacement / atr
        if displacement_ratio <= 1.5:
            score += 15
            breakdown["entry_timing"] = {
                "ok": True, "pts": 15,
                "detail": f"disp {displacement_ratio:.2f}× ATR ≤ 1.5×",
            }
        else:
            breakdown["entry_timing"] = {
                "ok": False, "pts": 0,
                "detail": f"late entry: disp {displacement_ratio:.2f}× ATR",
            }
    else:
        score += 8  # partial when no data
        breakdown["entry_timing"] = {"ok": True, "pts": 8, "detail": "no ATR data — partial"}

    # ── Filter 5: Regime WR history (10 pts) ──────────────────────────────
    regime = regime_info.get("regime", "UNKNOWN")
    reg_wr = regime_min_wr.get(regime.upper(), -1.0)
    if total_trades < 8 or reg_wr < 0:
        score += 10  # not enough data → allow
        breakdown["regime_wr"] = {"ok": True, "pts": 10, "detail": "insufficient data — allowed"}
    elif reg_wr >= 0.45:
        score += 10
        breakdown["regime_wr"] = {"ok": True, "pts": 10,
                                   "detail": f"{regime} WR={reg_wr:.0%}"}
    elif reg_wr >= 0.35:
        score += 5
        breakdown["regime_wr"] = {"ok": True, "pts": 5,
                                   "detail": f"{regime} WR={reg_wr:.0%} borderline"}
    else:
        breakdown["regime_wr"] = {"ok": False, "pts": 0,
                                   "detail": f"{regime} WR={reg_wr:.0%} too low"}

    # ── Filter 6: Hour of day (10 pts) ────────────────────────────────────
    now_hour = datetime.now(tz.utc).hour
    hour_key = f"H{now_hour:02d}"
    if hour_key in bad_hours:
        breakdown["hour_quality"] = {"ok": False, "pts": 0,
                                      "detail": f"{hour_key} is historically bad"}
    else:
        this_hour_wr = hour_wr.get(hour_key, 0.5)
        h_pts = 10 if this_hour_wr >= 0.45 else 5
        score += h_pts
        breakdown["hour_quality"] = {"ok": True, "pts": h_pts,
                                      "detail": f"{hour_key} WR={this_hour_wr:.0%}"}

    # ── Filter 7: HMM confidence (10 pts) ─────────────────────────────────
    hmm_conf = float(regime_info.get("hmm_confidence", 0))
    if hmm_conf >= 0.65:
        score += 10
        breakdown["hmm_confidence"] = {"ok": True, "pts": 10,
                                        "detail": f"HMM conf={hmm_conf:.0%}"}
    elif hmm_conf >= 0.45:
        score += 5
        breakdown["hmm_confidence"] = {"ok": True, "pts": 5,
                                        "detail": f"HMM conf={hmm_conf:.0%} moderate"}
    else:
        breakdown["hmm_confidence"] = {"ok": False, "pts": 0,
                                        "detail": f"HMM conf={hmm_conf:.0%} low (rule-based only)"}

    # ── Filter 8: Macro GenAI bias (5 pts) ────────────────────────────────
    try:
        r = await _get_redis()
        bias_raw = await r.get("nexus:genai:daily_bias")
        if bias_raw:
            bias_data = json.loads(bias_raw)
            bias = bias_data.get("bias", "NEUTRAL")
            bias_match = (
                (direction == "BUY" and bias == "BULLISH") or
                (direction == "SELL" and bias == "BEARISH") or
                bias == "NEUTRAL"
            )
            if bias_match:
                score += 5
                breakdown["macro_bias"] = {"ok": True, "pts": 5,
                                            "detail": f"GenAI bias={bias} aligns"}
            else:
                breakdown["macro_bias"] = {"ok": False, "pts": 0,
                                            "detail": f"GenAI bias={bias} opposes {direction}"}
        else:
            score += 5  # no brief = allow
            breakdown["macro_bias"] = {"ok": True, "pts": 5, "detail": "no GenAI brief yet"}
    except Exception:
        score += 5
        breakdown["macro_bias"] = {"ok": True, "pts": 5, "detail": "GenAI check skipped"}

    quality_payload = {
        "symbol": symbol,
        "direction": direction,
        "score": score,
        "max_score": 100,
        "threshold": thresholds.get("quality_score_threshold", 60),
        "passes": score >= thresholds.get("quality_score_threshold", 60),
        "breakdown": breakdown,
        "regime": regime,
        "session": session_value,
        "computed_at": datetime.now(tz.utc).isoformat(),
    }

    try:
        r = await _get_redis()
        await r.set(_QUALITY_KEY, json.dumps(quality_payload), ex=120)
    except Exception:
        pass

    return score, quality_payload


# ── HMM Regime Bootstrap ──────────────────────────────────────────────────


def _bootstrap_hmm_regime() -> None:
    """Train and persist an HMM regime detector on synthetic data if needed.

    Runs synchronously on startup (fast — sub-second on synthetic data).
    """
    hmm_path = Path(MODEL_DIR) / "hmm_regime.pkl"
    if hmm_path.exists():
        logger.info("HMM regime model already exists at %s — skipping bootstrap", hmm_path)
        return

    try:
        from .models.hmm_regime import HMMRegimeDetector
    except ImportError:
        logger.info("hmmlearn not available — skipping HMM bootstrap")
        return

    if not HMMRegimeDetector.is_available():
        logger.info("hmmlearn not installed — skipping HMM bootstrap")
        return

    try:
        _, _, ohlcv, _ = _generate_synthetic_data(n=5000)
        detector = HMMRegimeDetector()
        metrics = detector.fit(ohlcv)
        detector.save(hmm_path)
        logger.info("HMM regime model bootstrapped: %s", metrics)
    except Exception:
        logger.warning("HMM regime bootstrap failed", exc_info=True)


# ── Startup / Shutdown Hooks ───────────────────────────────────────────────


async def startup_traders() -> None:
    """Called on FastAPI startup to initialise traders, retrain scheduler, and autonomous loop."""
    global _retrain_scheduler, _autonomous_task
    cfg = get_current_config()
    logger.info(
        "Traders API starting in %s mode (TF=%s, retrain=%dh)",
        cfg.mode.value, cfg.operational_tf, cfg.retrain_interval_sec // 3600,
    )

    # Start trade memory system (background TRADE_CLOSE listener)
    await start_trade_memory()
    logger.info("Trade memory system started")

    # Pre-load models so first prediction is fast
    ml = _get_ml_trader()
    dl = _get_dl_trader()
    rl = _get_rl_trader()

    # Bootstrap training: if no models are fitted (first run), train immediately
    # so the system can trade from the very first moment.
    any_fitted = ml.is_fitted or dl.is_fitted or rl.is_fitted
    if not any_fitted:
        logger.info("No fitted models detected (first run) — running bootstrap training...")
        symbols = os.getenv("STREAM_SYMBOLS", "XAUUSD").split(",")
        await _run_training(["ml", "dl", "rl"], symbols, [cfg.operational_tf])
        logger.info("Bootstrap training complete — models ready for immediate trading")

    # Bootstrap HMM regime detector if no saved model exists
    _bootstrap_hmm_regime()

    # Try to immediately retrain HMM on real H1 data (replaces synthetic bootstrap).
    asyncio.create_task(_retrain_hmm_on_real_data())

    # Compute initial dynamic thresholds so dashboard sees them immediately.
    try:
        mem = get_trade_memory()
        if mem._trades:
            # Always recompute metrics on startup — backfill populates trades
            # but doesn't persist metrics yet
            mem._recompute_metrics()
            # Persist to Redis so future restarts load from cache, not API
            await mem._persist()
            logger.info(
                "trade_memory startup: %d trades, WR=%.0f%%",
                len(mem._trades),
                mem._metrics.get("overall_win_rate", 0) * 100,
            )
        await mem.compute_dynamic_thresholds()
    except Exception:
        logger.debug("trade_memory startup init failed", exc_info=True)

    # Start auto-retrain scheduler
    _retrain_scheduler = AutoRetrainScheduler(
        retrain_interval_sec=cfg.retrain_interval_sec,
        retrain_callback=_auto_retrain_callback,
    )
    _retrain_scheduler.start()
    logger.info("Auto-retrain scheduler started (every %ds)", cfg.retrain_interval_sec)

    # Start autonomous ML trading pipeline (parallel to GenAI agents)
    auto_enabled = os.getenv("NEXUS_AUTONOMOUS_ML", "true").lower() == "true"
    if auto_enabled:
        _autonomous_task = asyncio.create_task(_autonomous_trading_loop())
        logger.info("Autonomous ML trading pipeline started")

    # Pre-load lifecycle agent (loads from disk if available)
    _get_lifecycle_agent()

    # Retrain lifecycle model if TradeMemory has enough records
    asyncio.create_task(_retrain_lifecycle_if_enough_data())

    # Start lifecycle position monitor
    global _lifecycle_task
    _lifecycle_task = asyncio.create_task(_lifecycle_monitor())
    logger.info("RL lifecycle position monitor started")


async def shutdown_traders() -> None:
    """Called on FastAPI shutdown to cleanly stop background tasks."""
    global _redis_pool
    await stop_trade_memory()
    logger.info("Trade memory system stopped")
    if _retrain_scheduler:
        _retrain_scheduler.stop()
        logger.info("Auto-retrain scheduler stopped")
    if _autonomous_task and not _autonomous_task.done():
        _autonomous_task.cancel()
        _autonomous_stats["running"] = False
        logger.info("Autonomous ML pipeline stopped")
    if _lifecycle_task and not _lifecycle_task.done():
        _lifecycle_task.cancel()
        logger.info("RL lifecycle monitor stopped")
    if _redis_pool is not None:
        await _redis_pool.aclose()
        _redis_pool = None
        logger.info("Traders Redis pool closed")
