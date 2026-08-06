"""
Nexus ML — FastAPI service (port 8003).

Exposes REST endpoints for prediction, training, and model status.
Runs a background Redis Stream consumer that listens for FeatureEvents,
runs the ensemble, and publishes SignalEvents.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
import redis.asyncio as aioredis
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.constants import (
    HEARTBEAT_INTERVAL_SEC,
    STREAM_MAX_LEN,
    RedisStream,
    ServiceName,
    Verdict,
)
from shared.events import FeatureEvent, SignalEvent

from .ensemble import EnsembleSignal, ModelEnsemble
from .rule_based_signal import RuleBasedSignalGenerator
from .training import WalkForwardConfig, WalkForwardTrainer

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
MODEL_DIR = os.getenv("MODEL_DIR", "./models_store")
CONSUMER_GROUP = "nexus_ml_group"
CONSUMER_NAME = "nexus_ml_worker_1"

# ── Pydantic request / response schemas ──────────────────────────────────────


class PredictResponse(BaseModel):
    symbol: str
    direction: str
    confidence: float
    regime: str
    anomaly_score: float
    is_anomaly: bool
    individual_scores: dict[str, float] = Field(default_factory=dict)
    sentiment: dict[str, float] = Field(default_factory=dict)
    top_features: list[str] = Field(default_factory=list)


class TrainRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["EURUSD"])
    train_months: int = 6
    val_months: int = 1
    step_months: int = 1


class TrainResponse(BaseModel):
    status: str
    message: str
    task_id: str = ""


class ModelStatusResponse(BaseModel):
    models: dict[str, bool]
    ensemble_loaded: bool
    rule_based_active: bool = False
    symbol: str = ""
    per_symbol: dict[str, dict[str, bool]] = {}


class HealthResponse(BaseModel):
    service: str
    status: str
    uptime_seconds: float
    models_loaded: dict[str, bool]


# ── Application state ────────────────────────────────────────────────────────

_ensemble: ModelEnsemble | None = None
_rule_based: RuleBasedSignalGenerator | None = None
_redis: aioredis.Redis | None = None
_start_time: float = 0.0
_consumer_task: asyncio.Task[None] | None = None
_training_in_progress: bool = False
_last_signal_ts: dict[str, float] = {}
_last_close_per_symbol: dict[str, float] = {}  # for label computation
SIGNAL_COOLDOWN_SEC = int(os.getenv("NEXUS_ML_SIGNAL_COOLDOWN_SEC", "60"))

# Auto-retrain config
_RETRAIN_MIN_SAMPLES = int(os.getenv("NEXUS_RETRAIN_MIN_SAMPLES", "500"))
_RETRAIN_CHECK_INTERVAL = int(os.getenv("NEXUS_RETRAIN_CHECK_INTERVAL_SEC", "3600"))
_RETRAIN_COOLDOWN_HOURS = int(os.getenv("NEXUS_RETRAIN_COOLDOWN_HOURS", "24"))
_RETRAIN_SYMBOLS = [
    s.strip().upper()
    for s in os.getenv("NEXUS_SYMBOLS", os.getenv("STREAM_SYMBOLS", "XAUUSD")).split(",")
    if s.strip()
]
_RETRAIN_LAST_KEY = "nexus:ml:last_retrain_ts"

# ── Per-symbol model registry ────────────────────────────────────────────────
# Each symbol trains and loads its own ensemble from models_store/<SYMBOL>/.
# Gold ($4k, ATR ~$5) and BTC ($64k, ATR ~$31) have incomparable scales — a
# model fitted on one produces meaningless predictions for the other.

_ensembles: dict[str, ModelEnsemble] = {}


def symbol_model_dir(symbol: str) -> Path:
    return Path(MODEL_DIR) / symbol.strip().upper()


def _get_symbol_ensemble(symbol: str) -> ModelEnsemble:
    """Return (creating+loading on first use) the ensemble for a symbol."""
    key = symbol.strip().upper()
    ens = _ensembles.get(key)
    if ens is None:
        ens = ModelEnsemble(model_dir=str(symbol_model_dir(key)))
        try:
            ens.load_all_models()
        except Exception:
            logger.warning("Ensemble load failed for %s", key, exc_info=True)
        _ensembles[key] = ens
    return ens


# ── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ensemble, _rule_based, _redis, _start_time, _consumer_task

    _start_time = time.time()
    from shared import setup_logger as _setup_logger
    _setup_logger("nexus-ml", level=os.getenv("LOG_LEVEL", "INFO"))

    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    logger.info("Connected to Redis at %s", REDIS_URL)

    # Per-symbol ensembles: each symbol keeps its own trained models under
    # models_store/<SYMBOL>/. Gold and BTC have completely different price
    # scales and dynamics, so one shared model would predict garbage for
    # whichever symbol it wasn't fitted on. _ensemble stays bound to the
    # first configured symbol for backwards compatibility with callers that
    # don't pass one.
    for sym in _RETRAIN_SYMBOLS:
        ens = _get_symbol_ensemble(sym)
        logger.info("Ensemble[%s] model load status: %s", sym, ens.models_status())
    _ensemble = _get_symbol_ensemble(_RETRAIN_SYMBOLS[0]) if _RETRAIN_SYMBOLS else ModelEnsemble(model_dir=MODEL_DIR)
    status = _ensemble.models_status()

    # Rule-based fallback for when no ML models are trained yet
    _rule_based = RuleBasedSignalGenerator(confidence_threshold=0.35)
    any_model_loaded = any(status.values())
    if not any_model_loaded:
        logger.warning(
            "No ML models loaded — using rule-based signal generator (real indicators)"
        )

    await _ensure_consumer_group()
    _consumer_task = asyncio.create_task(_stream_consumer())
    asyncio.create_task(_heartbeat_loop())
    asyncio.create_task(_auto_retrain_loop())

    logger.info("nexus-ml service started on port 8003")

    # Start parallel model traders (ML/DL/RL) background scheduler
    try:
        from .traders_api import startup_traders, shutdown_traders
        await startup_traders()
        logger.info("Parallel model traders initialised")
    except Exception as _te:
        logger.warning("Traders startup skipped: %s", _te)

    yield

    # Shutdown traders scheduler
    try:
        from .traders_api import shutdown_traders
        await shutdown_traders()
    except Exception:
        pass

    if _consumer_task:
        _consumer_task.cancel()
    if _redis:
        await _redis.aclose()
    logger.info("nexus-ml service shut down")


app = FastAPI(
    title="Nexus ML Pipeline",
    version="2.0.0",
    lifespan=lifespan,
)

# ── Parallel Model Traders (ML / DL / RL) ───────────────────────────────────
try:
    from .traders_api import router as traders_router
    app.include_router(traders_router)
    logger.info("Parallel model traders API mounted at /api/traders")
except Exception as _traders_exc:
    logger.warning("Traders API not available: %s", _traders_exc)


# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        service=ServiceName.ML,
        status="healthy",
        uptime_seconds=round(time.time() - _start_time, 2),
        models_loaded=_ensemble.models_status() if _ensemble else {},
    )


# ── Prediction endpoint ──────────────────────────────────────────────────────


@app.get("/api/ml/predict/{symbol}", response_model=PredictResponse)
async def predict(symbol: str):
    features = await _get_latest_features(symbol)
    if features is None:
        raise HTTPException(404, f"No cached features for {symbol}")

    ensemble = _get_symbol_ensemble(symbol)
    any_model_loaded = any(ensemble.models_status().values())

    if any_model_loaded:
        feature_array = ensemble.build_feature_array(features)
        signal = ensemble.predict(feature_array)
    elif _rule_based is not None:
        signal = _rule_based.predict(features)
    else:
        raise HTTPException(503, "No signal generator available")

    return PredictResponse(
        symbol=symbol,
        direction=signal.direction,
        confidence=signal.confidence,
        regime=signal.regime,
        anomaly_score=signal.anomaly_score,
        is_anomaly=signal.is_anomaly,
        individual_scores=signal.individual_scores,
        sentiment=signal.sentiment,
        top_features=signal.top_features,
    )


# ── Training endpoint ────────────────────────────────────────────────────────


@app.post("/api/ml/train", response_model=TrainResponse)
async def train(req: TrainRequest, background_tasks: BackgroundTasks):
    global _training_in_progress

    if _training_in_progress:
        raise HTTPException(409, "Training already in progress")

    import uuid
    task_id = uuid.uuid4().hex[:12]
    _training_in_progress = True

    background_tasks.add_task(_run_training, req, task_id)

    return TrainResponse(
        status="started",
        message=f"Walk-forward training initiated for {req.symbols}",
        task_id=task_id,
    )


async def _run_training(req: TrainRequest, task_id: str) -> None:
    """Train each requested symbol into its OWN models_store/<SYMBOL>/ dir.

    Training per symbol (rather than pooling every symbol into one model set)
    keeps an existing symbol's models untouched when another is retrained.
    """
    global _training_in_progress
    try:
        logger.info("[%s] Training started for %s", task_id, req.symbols)

        for symbol in req.symbols:
            sym = symbol.strip().upper()
            out_dir = symbol_model_dir(sym)
            out_dir.mkdir(parents=True, exist_ok=True)

            data = await _fetch_training_data([sym])
            if data is None:
                logger.error("[%s] No training data for %s — skipped", task_id, sym)
                continue

            cfg = WalkForwardConfig(
                train_months=req.train_months,
                val_months=req.val_months,
                step_months=req.step_months,
                model_dir=str(out_dir),
            )
            trainer = WalkForwardTrainer(cfg)

            X, y, timestamps, feature_names = data
            results = trainer.train_all_models(X, y, timestamps, feature_names)
            logger.info("[%s] %s trained — %d folds → %s", task_id, sym, len(results), out_dir)

            # Reload just this symbol's ensemble; others stay as they are.
            _ensembles.pop(sym, None)
            reloaded = _get_symbol_ensemble(sym)
            logger.info("[%s] %s ensemble reloaded: %s", task_id, sym, reloaded.models_status())

    except Exception:
        logger.error("[%s] Training failed", task_id, exc_info=True)
    finally:
        _training_in_progress = False


# ── Model status endpoint ────────────────────────────────────────────────────


@app.get("/api/ml/models/status", response_model=ModelStatusResponse)
async def models_status(symbol: str | None = None):
    """Model status. Without `symbol` reports the first configured symbol;
    `per_symbol` always carries every configured symbol's own ensemble."""
    target = (symbol or (_RETRAIN_SYMBOLS[0] if _RETRAIN_SYMBOLS else "XAUUSD")).upper()
    status = _get_symbol_ensemble(target).models_status()
    any_loaded = any(status.values())
    per_symbol = {
        sym: _get_symbol_ensemble(sym).models_status()
        for sym in _RETRAIN_SYMBOLS
    }
    return ModelStatusResponse(
        models=status,
        ensemble_loaded=any_loaded,
        rule_based_active=not any_loaded and _rule_based is not None,
        symbol=target,
        per_symbol=per_symbol,
    )


@app.get("/api/ml/retrain/status")
async def retrain_status():
    """Auto-retrain state: last run, sample counts, next trigger threshold."""
    if _redis is None:
        raise HTTPException(503, "Redis not connected")

    total_samples = 0
    per_symbol: dict[str, int] = {}
    for sym in _RETRAIN_SYMBOLS:
        n = await _redis.llen(f"nexus:features:history:{sym}")
        per_symbol[sym] = n
        total_samples += n

    last_result_raw = await _redis.get("nexus:ml:last_retrain_result")
    last_result = json.loads(last_result_raw) if last_result_raw else None

    last_ts_raw = await _redis.get(_RETRAIN_LAST_KEY)
    last_ts = float(last_ts_raw) if last_ts_raw else None

    return {
        "total_samples": total_samples,
        "per_symbol": per_symbol,
        "min_samples_required": _RETRAIN_MIN_SAMPLES,
        "ready_to_train": total_samples >= _RETRAIN_MIN_SAMPLES and not _training_in_progress,
        "training_in_progress": _training_in_progress,
        "last_retrain_ts": last_ts,
        "last_retrain_result": last_result,
        "retrain_cooldown_hours": _RETRAIN_COOLDOWN_HOURS,
        "check_interval_sec": _RETRAIN_CHECK_INTERVAL,
    }


# ── Redis Stream consumer ────────────────────────────────────────────────────


async def _ensure_consumer_group() -> None:
    assert _redis is not None
    try:
        await _redis.xgroup_create(
            RedisStream.FEATURE_STREAM, CONSUMER_GROUP, id="0", mkstream=True,
        )
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def _stream_consumer() -> None:
    """Continuously read FeatureEvents, run predictions, publish SignalEvents."""
    assert _redis is not None
    assert _ensemble is not None or _rule_based is not None

    logger.info("Stream consumer started — listening on %s", RedisStream.FEATURE_STREAM)

    while True:
        try:
            entries = await _redis.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername=CONSUMER_NAME,
                streams={RedisStream.FEATURE_STREAM: ">"},
                count=10,
                block=2000,
            )

            if not entries:
                continue

            for _stream_name, messages in entries:
                for msg_id, raw in messages:
                    await _handle_feature_event(msg_id, raw)

        except asyncio.CancelledError:
            logger.info("Stream consumer cancelled")
            break
        except Exception:
            logger.error("Stream consumer error — retrying in 5s", exc_info=True)
            await asyncio.sleep(5)


async def _handle_feature_event(msg_id: str, raw: dict[str, str]) -> None:
    assert _redis is not None and _ensemble is not None

    try:
        symbol = raw.get("symbol", "")
        timeframe = str(raw.get("timeframe", "")).upper()

        # Features can arrive in two formats:
        # 1. Single JSON blob under key "features"
        # 2. Flat hash keys like "features.ema_9", "features.rsi", etc.
        features_str = raw.get("features")
        if features_str is not None:
            # Format 1: nested JSON string
            if isinstance(features_str, str):
                features_dict = json.loads(features_str.replace("'", '"'))
            else:
                features_dict = features_str
        else:
            # Format 2: extract from flat "features.*" keys
            prefix = "features."
            features_dict = {}
            for k, v in raw.items():
                if k.startswith(prefix):
                    feat_name = k[len(prefix):]
                    try:
                        features_dict[feat_name] = float(v)
                    except (ValueError, TypeError):
                        features_dict[feat_name] = v  # keep strings like trend_direction

        if not features_dict:
            logger.warning("Empty features for %s — skipping event %s", symbol, msg_id)
            await _redis.xack(RedisStream.FEATURE_STREAM, CONSUMER_GROUP, msg_id)
            return

        # Recompute dynamic TF on M5 probe bars (scalping anchor)
        if timeframe == "M5":
            try:
                from datetime import datetime, timezone

                from services.nexus_ml.traders.trading_mode import (
                    detect_session,
                    get_current_config,
                    select_operational_tf,
                )

                cfg = get_current_config()
                session = detect_session(datetime.now(timezone.utc).hour)
                spread = float(features_dict.get("spread", features_dict.get("spread_pips", 0)) or 0)
                avg_sp = float(features_dict.get("avg_spread", spread) or spread or 1.0)
                auto = True
                try:
                    from shared import runtime_settings as _rs
                    auto = bool(_rs.get_cached_settings().get("auto_tf_selection", True))
                except Exception:
                    pass
                sel = select_operational_tf(
                    cfg, session, features_dict, auto=auto,
                    current_spread=spread, avg_spread=avg_sp,
                )
                from datetime import timezone as tz
                await _redis.set(
                    "nexus:dynamic:tf",
                    json.dumps({
                        "operational_tf": sel.operational_tf,
                        "confirmation_tfs": sel.confirmation_tfs,
                        "base_tf": sel.base_tf,
                        "reason": sel.reason,
                        "vol_regime": sel.vol_regime,
                        "session": sel.session,
                        "auto_selected": sel.auto_selected,
                        "updated_at": datetime.now(tz.utc).isoformat(),
                    }),
                    ex=180,
                )
            except Exception:
                logger.debug("dynamic_tf update skipped", exc_info=True)

        # Only signal on the live operational TF to avoid triple-firing.
        operational_tf = os.getenv("NEXUS_OPERATIONAL_TF", "M5").upper()
        try:
            dyn_raw = await _redis.get("nexus:dynamic:tf")
            if dyn_raw:
                operational_tf = str(json.loads(dyn_raw).get("operational_tf", operational_tf)).upper()
        except Exception:
            pass
        if timeframe and timeframe != operational_tf:
            await _redis.xack(RedisStream.FEATURE_STREAM, CONSUMER_GROUP, msg_id)
            return

        now = time.time()
        last = _last_signal_ts.get(symbol, 0.0)
        if last and (now - last) < SIGNAL_COOLDOWN_SEC:
            await _redis.xack(RedisStream.FEATURE_STREAM, CONSUMER_GROUP, msg_id)
            return

        # Decide: this symbol's ML ensemble (if trained) or rule-based fallback
        ensemble = _get_symbol_ensemble(symbol)
        any_model_loaded = any(ensemble.models_status().values())

        if any_model_loaded:
            # Use trained ML models for THIS symbol
            feature_array = ensemble.build_feature_array(features_dict)
            signal: EnsembleSignal = ensemble.predict(feature_array)
            signal_source = "ml_ensemble"
        elif _rule_based is not None:
            # Fallback: rule-based signal from real technical indicators
            signal = _rule_based.predict(features_dict)
            signal_source = "rule_based"
        else:
            # Should never happen — both are None
            logger.error("No signal generator available — skipping event %s", msg_id)
            await _redis.xack(RedisStream.FEATURE_STREAM, CONSUMER_GROUP, msg_id)
            return

        direction_map = {"BUY": Verdict.BUY, "SELL": Verdict.SELL}
        verdict = direction_map.get(signal.direction, Verdict.NO_TRADE)

        # Filter to numeric-only for SignalEvent (Pydantic expects dict[str, float])
        numeric_features = {k: v for k, v in features_dict.items() if isinstance(v, (int, float))}

        # Accumulate feature history for model training on EVERY processed bar —
        # not just bars that happen to produce an actionable BUY/SELL verdict.
        # Gating this behind the NO_TRADE return starved the walk-forward
        # trainer's min_train_samples threshold for weeks (rule-based/ensemble
        # signals are NO_TRADE most of the time by design).
        try:
            from datetime import datetime, timezone as _tz
            close = float(features_dict.get("close", 0))
            # Label: compare current close to previous bar's close for this symbol.
            # prev_close is NOT in the feature dict (nexus-features doesn't expose it),
            # so we track the last seen close in-process.
            prev_close = _last_close_per_symbol.get(symbol, close)
            label = 1 if close > prev_close else 0
            _last_close_per_symbol[symbol] = close
            ohlcv = {
                "open": float(features_dict.get("open", 0)),
                "high": float(features_dict.get("high", 0)),
                "low": float(features_dict.get("low", 0)),
                "close": close,
                "volume": float(features_dict.get("volume", 0)),
            }
            history_key = f"nexus:features:history:{symbol}"
            # Timestamp: features don't carry a bar timestamp; use the event's
            # arrival time (UTC) so walk-forward folds can group by month.
            ts = (
                features_dict.get("timestamp")
                or raw.get("timestamp")
                or datetime.now(_tz.utc).isoformat()
            )
            record = json.dumps({
                "features": numeric_features,
                "label": label,
                "ohlcv": ohlcv,
                "timestamp": ts,
            })
            await _redis.rpush(history_key, record)
            await _redis.ltrim(history_key, -10000, -1)
        except Exception:
            logger.debug("Failed to accumulate feature history", exc_info=True)

        if verdict == Verdict.NO_TRADE:
            await _redis.xack(RedisStream.FEATURE_STREAM, CONSUMER_GROUP, msg_id)
            return

        event = SignalEvent(
            source=ServiceName.ML,
            symbol=symbol,
            direction=verdict,
            confidence=signal.confidence,
            features=numeric_features,
            ml_scores=signal.individual_scores,
        )

        await _redis.xadd(
            RedisStream.SIGNAL_STREAM,
            event.to_stream_dict(),
            maxlen=STREAM_MAX_LEN,
        )

        _last_signal_ts[symbol] = now

        logger.info(
            "Signal published [%s]: %s %s conf=%.3f regime=%s anomaly=%s",
            signal_source, symbol, signal.direction, signal.confidence,
            signal.regime, signal.is_anomaly,
        )

    except Exception:
        logger.error("Failed to handle feature event %s", msg_id, exc_info=True)
    finally:
        await _redis.xack(RedisStream.FEATURE_STREAM, CONSUMER_GROUP, msg_id)


# ── Helper functions ──────────────────────────────────────────────────────────


async def _get_latest_features(symbol: str) -> dict[str, float] | None:
    assert _redis is not None
    # Try common timeframes — features are cached with timeframe suffix
    for tf in ("M15", "H1", "M5", "M1"):
        cache_key = f"nexus:features:{symbol}:{tf}"
        raw = await _redis.get(cache_key)
        if raw is not None:
            try:
                data = json.loads(raw)
                if isinstance(data, dict) and "features" in data:
                    return data["features"]
                return data
            except (json.JSONDecodeError, TypeError):
                continue
    return None


async def _fetch_training_data(
    symbols: list[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]] | None:
    """Fetch historical feature data from Redis/DuckDB for training.

    Returns (X, y, timestamps, feature_names) or None if no data.
    """
    assert _redis is not None

    all_features: list[list[float]] = []
    all_labels: list[int] = []
    all_timestamps: list[str] = []
    feature_names: list[str] = []

    for symbol in symbols:
        history_key = f"nexus:features:history:{symbol}"
        raw_list = await _redis.lrange(history_key, 0, -1)

        for raw in raw_list:
            try:
                record = json.loads(raw)
                feats = record.get("features", {})
                label = record.get("label")
                ts = record.get("timestamp", "")

                if not feats or label is None:
                    continue

                if not feature_names:
                    feature_names = list(feats.keys())

                all_features.append([feats.get(k, 0.0) for k in feature_names])
                all_labels.append(int(label))
                all_timestamps.append(ts)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue

    if not all_features:
        return None

    X = np.array(all_features, dtype=np.float64)
    y = np.array(all_labels, dtype=np.int64)
    timestamps = np.array(all_timestamps, dtype="datetime64[s]")

    return X, y, timestamps, feature_names


async def _auto_retrain_loop() -> None:
    """Background task: trigger ensemble retraining when enough new feature data accumulates.

    Checks every NEXUS_RETRAIN_CHECK_INTERVAL_SEC seconds. Skips if:
    - Less than NEXUS_RETRAIN_MIN_SAMPLES records in feature history
    - Less than NEXUS_RETRAIN_COOLDOWN_HOURS since the last retrain
    - Training is already in progress
    """
    global _training_in_progress, _ensemble

    await asyncio.sleep(60)  # Let the service fully start before first check

    while True:
        try:
            if _redis is None or _training_in_progress:
                await asyncio.sleep(_RETRAIN_CHECK_INTERVAL)
                continue

            # Check cooldown
            last_ts_raw = await _redis.get(_RETRAIN_LAST_KEY)
            if last_ts_raw:
                elapsed_h = (time.time() - float(last_ts_raw)) / 3600
                if elapsed_h < _RETRAIN_COOLDOWN_HOURS:
                    await asyncio.sleep(_RETRAIN_CHECK_INTERVAL)
                    continue

            # Check sample count across all symbols
            total_samples = 0
            for sym in _RETRAIN_SYMBOLS:
                key = f"nexus:features:history:{sym}"
                count = await _redis.llen(key)
                total_samples += count

            if total_samples < _RETRAIN_MIN_SAMPLES:
                logger.info(
                    "auto_retrain.skipped samples=%d/%d",
                    total_samples, _RETRAIN_MIN_SAMPLES,
                )
                await asyncio.sleep(_RETRAIN_CHECK_INTERVAL)
                continue

            logger.info(
                "auto_retrain.triggered samples=%d symbols=%s",
                total_samples, _RETRAIN_SYMBOLS,
            )

            import uuid
            task_id = f"auto_{uuid.uuid4().hex[:8]}"
            _training_in_progress = True

            try:
                # Retrain each symbol into its own models_store/<SYMBOL>/ so a
                # BTC retrain never overwrites the gold models (and vice versa).
                total_folds = 0
                new_status: dict[str, dict[str, bool]] = {}
                for sym in _RETRAIN_SYMBOLS:
                    old_status = _get_symbol_ensemble(sym).models_status()

                    data = await _fetch_training_data([sym])
                    if data is None:
                        logger.warning("auto_retrain.no_data symbol=%s", sym)
                        continue

                    out_dir = symbol_model_dir(sym)
                    out_dir.mkdir(parents=True, exist_ok=True)

                    X, y, timestamps, feature_names = data
                    cfg = WalkForwardConfig(
                        train_months=6,
                        val_months=1,
                        step_months=2,
                        model_dir=str(out_dir),
                    )
                    trainer = WalkForwardTrainer(cfg)
                    results = trainer.train_all_models(X, y, timestamps, feature_names)
                    total_folds += len(results)

                    _ensembles.pop(sym, None)
                    reloaded = _get_symbol_ensemble(sym).models_status()
                    new_status[sym] = reloaded
                    logger.info(
                        "auto_retrain.symbol_done symbol=%s folds=%d old=%s new=%s",
                        sym, len(results), old_status, reloaded,
                    )

                logger.info("auto_retrain.complete folds=%d task=%s", total_folds, task_id)
                await _redis.set(_RETRAIN_LAST_KEY, str(time.time()), ex=86_400 * 7)

                # Publish retrain event to Redis for dashboard visibility
                try:
                    await _redis.set(
                        "nexus:ml:last_retrain_result",
                        json.dumps({
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "samples": int(total_samples),
                            "folds": total_folds,
                            "models": new_status,
                        }),
                        ex=86_400 * 2,
                    )
                except Exception:
                    pass

            finally:
                _training_in_progress = False

        except asyncio.CancelledError:
            break
        except Exception:
            logger.error("auto_retrain.error", exc_info=True)

        await asyncio.sleep(_RETRAIN_CHECK_INTERVAL)


async def _heartbeat_loop() -> None:
    assert _redis is not None
    while True:
        try:
            from shared.events import SystemHealthEvent
            import psutil

            event = SystemHealthEvent(
                source=ServiceName.ML,
                service_name=ServiceName.ML,
                status="healthy",
                cpu_pct=psutil.cpu_percent(),
                ram_pct=psutil.virtual_memory().percent,
            )
            await _redis.xadd(
                RedisStream.SYSTEM_HEALTH_STREAM,
                event.to_stream_dict(),
                maxlen=STREAM_MAX_LEN,
            )
        except Exception:
            logger.debug("Heartbeat failed", exc_info=True)

        await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "services.nexus_ml.main:app",
        host="0.0.0.0",
        port=8003,
        reload=False,
        log_level="info",
    )
