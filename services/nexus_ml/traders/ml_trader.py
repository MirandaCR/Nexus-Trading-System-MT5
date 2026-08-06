"""
ML Trader — Gradient-boosted ensemble with SL/TP-aware features.

Uses XGBoost for direction prediction and a secondary LightGBM model
for SL/TP distance regression.  Multi-timeframe features are flattened
into a single vector so the model sees H1/H4/D1 context alongside
the operational timeframe.
"""
from __future__ import annotations

import logging
import pickle
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.exceptions import NotFittedError

# ── Benign sklearn noise ─────────────────────────────────────────────────────
# These filters are process-wide (this module is imported at nexus-ml startup)
# and each targets one exact message, so no other warning is hidden.

# 1. The SL/TP regressors are fitted once from a named-column frame but queried
#    per-bar with a bare ordered numpy array (feature order is identical because
#    both paths use this trader's own build_mtf_features/compute_sl_tp_features
#    builders), so sklearn warns on every single prediction.
warnings.filterwarnings(
    "ignore",
    message="X does not have valid feature names",
    category=UserWarning,
)

# 2. LightGBM/XGBoost call sklearn's `delayed` with joblib's own `Parallel`
#    during fit. sklearn then warns that its per-thread config may not reach the
#    workers — irrelevant here (no custom sklearn config is set) and emitted
#    once per tree, which floods the log on every retrain.
warnings.filterwarnings(
    "ignore",
    message=r"`sklearn\.utils\.parallel\.delayed` should be used with",
    category=UserWarning,
)

from .base_trader import (
    BaseModelTrader,
    ModelPerformance,
    TradeSignal,
    build_mtf_features,
    compute_sl_tp_features,
    get_pip_value,
)

logger = logging.getLogger(__name__)

try:
    import xgboost as xgb

    _HAS_XGB = True
except ImportError:
    _HAS_XGB = False
    xgb = None  # type: ignore[assignment]

try:
    import lightgbm as lgb

    _HAS_LGB = True
except ImportError:
    _HAS_LGB = False
    lgb = None  # type: ignore[assignment]


class MLTrader(BaseModelTrader):
    """XGBoost direction + LightGBM SL/TP regression."""

    def __init__(
        self,
        model_dir: str | Path = "./models_store/traders",
        confidence_threshold: float = 0.55,
    ) -> None:
        super().__init__("ml", "XGB_LGBM_SLTP", model_dir)
        self.confidence_threshold = confidence_threshold
        self._dir_model: Any = None     # XGBoost classifier
        self._sl_model: Any = None      # LightGBM regressor (SL pips)
        self._tp_model: Any = None      # LightGBM regressor (TP pips)
        self._feature_names: list[str] = []
        self._regime_model: Any = None  # Optional regime classifier

    # ── Training ─────────────────────────────────────────────────────────

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        ohlcv: np.ndarray | None = None,
        feature_names: list[str] | None = None,
        sl_targets: np.ndarray | None = None,
        tp_targets: np.ndarray | None = None,
        **kwargs: Any,
    ) -> dict[str, float]:
        """Train direction classifier and SL/TP regressors.

        Parameters
        ----------
        X : (N, F) feature matrix (can include MTF features)
        y : (N,) binary labels — 1=UP, 0=DOWN
        ohlcv : (N, 5) — used to compute SL/TP targets if not provided
        feature_names : column names for X
        sl_targets : (N,) precomputed SL pip distances, or None
        tp_targets : (N,) precomputed TP pip distances, or None
        """
        t0 = time.time()
        metrics: dict[str, float] = {}

        if feature_names:
            self._feature_names = list(feature_names)

        # ── Direction model (XGBoost) ──
        if _HAS_XGB:
            dir_params = {
                "max_depth": 6,
                "learning_rate": 0.05,
                "n_estimators": 300,
                "subsample": 0.8,
                "colsample_bytree": 0.8,
                "eval_metric": "logloss",
                "use_label_encoder": False,
                "tree_method": "hist",
                "verbosity": 0,
            }
            self._dir_model = xgb.XGBClassifier(**dir_params)
            self._dir_model.fit(X, y)

            preds = self._dir_model.predict(X)
            train_acc = float(np.mean(preds == y))
            metrics["direction_train_acc"] = train_acc
            logger.info("ML Trader direction model trained: acc=%.4f", train_acc)
        else:
            logger.warning("XGBoost not available — using random baseline for direction")

        # ── SL/TP regression (LightGBM) ──
        if ohlcv is not None and _HAS_LGB:
            if sl_targets is None or tp_targets is None:
                train_symbol = kwargs.get("symbol", "")
                sl_targets, tp_targets = self._compute_sl_tp_targets(ohlcv, y, symbol=train_symbol)

            valid_mask = (sl_targets > 0) & (tp_targets > 0) & np.isfinite(sl_targets) & np.isfinite(tp_targets)
            X_valid = X[valid_mask]
            sl_valid = sl_targets[valid_mask]
            tp_valid = tp_targets[valid_mask]

            if len(X_valid) > 100:
                lgb_params = {
                    "n_estimators": 200,
                    "max_depth": 5,
                    "learning_rate": 0.05,
                    "subsample": 0.8,
                    "verbose": -1,
                }
                self._sl_model = lgb.LGBMRegressor(**lgb_params)
                self._sl_model.fit(X_valid, sl_valid)
                sl_pred = self._sl_model.predict(X_valid)
                sl_mae = float(np.mean(np.abs(sl_pred - sl_valid)))
                metrics["sl_mae_pips"] = sl_mae

                self._tp_model = lgb.LGBMRegressor(**lgb_params)
                self._tp_model.fit(X_valid, tp_valid)
                tp_pred = self._tp_model.predict(X_valid)
                tp_mae = float(np.mean(np.abs(tp_pred - tp_valid)))
                metrics["tp_mae_pips"] = tp_mae

                logger.info("ML Trader SL/TP models trained: SL MAE=%.1f, TP MAE=%.1f pips", sl_mae, tp_mae)

        self._is_fitted = True
        elapsed = time.time() - t0
        self._performance.training_time_sec = elapsed
        self._performance.train_samples = len(X)
        self._performance.last_trained = datetime.now(timezone.utc).isoformat()
        metrics["training_time_sec"] = elapsed
        return metrics

    def _compute_sl_tp_targets(
        self,
        ohlcv: np.ndarray,
        y: np.ndarray,
        symbol: str = "",
    ) -> tuple[np.ndarray, np.ndarray]:
        """Derive SL/TP targets from future price action using ATR-based approach."""
        n = len(ohlcv)
        pip_value = get_pip_value(symbol) if symbol else self._infer_pip_value(ohlcv)

        # Compute ATR(14) for each bar
        atr = np.zeros(n)
        for i in range(1, n):
            tr = max(
                ohlcv[i, 1] - ohlcv[i, 2],                  # high - low
                abs(ohlcv[i, 1] - ohlcv[i - 1, 3]),          # high - prev_close
                abs(ohlcv[i, 2] - ohlcv[i - 1, 3]),          # low - prev_close
            )
            if i < 14:
                atr[i] = tr
            else:
                atr[i] = (atr[i - 1] * 13 + tr) / 14

        sl_pips = atr / pip_value * 1.0   # 1x ATR for SL
        tp_pips = atr / pip_value * 2.0   # 2x ATR for TP

        # Ensure minimum values
        sl_pips = np.maximum(sl_pips, 5.0)
        tp_pips = np.maximum(tp_pips, 10.0)

        return sl_pips, tp_pips

    @staticmethod
    def _infer_pip_value(ohlcv: np.ndarray) -> float:
        """Infer pip value from price magnitude when symbol is unknown."""
        if len(ohlcv) == 0:
            return 0.0001
        median_price = float(np.median(ohlcv[:, 3]))  # median close
        if median_price > 500:       # gold, indices
            return 0.01
        elif median_price > 10:      # JPY pairs, silver
            return 0.01
        return 0.0001

    # ── Prediction ───────────────────────────────────────────────────────

    def predict(
        self,
        features: np.ndarray | dict[str, float],
        symbol: str = "",
    ) -> TradeSignal:
        if not self._is_fitted or self._dir_model is None:
            return TradeSignal(
                model_type="ml", model_name=self.model_name,
                symbol=symbol, direction="NO_TRADE", confidence=0.0,
                reasoning="Model not trained",
            )

        if isinstance(features, dict):
            if self._feature_names:
                feat_array = np.array([features.get(k, 0.0) for k in self._feature_names]).reshape(1, -1)
            else:
                feat_array = np.array(list(features.values())).reshape(1, -1)
        else:
            feat_array = features.reshape(1, -1) if features.ndim == 1 else features

        # Direction prediction. During an in-place retrain the booster is
        # momentarily un-fitted (new estimator created but .fit() not done yet);
        # a concurrent predict from the autonomous loop would raise
        # NotFittedError. Treat that transient window as NO_TRADE instead of
        # crashing the cycle and spamming a traceback.
        try:
            prob = self._dir_model.predict_proba(feat_array)[0]
        except NotFittedError:
            return TradeSignal(
                model_type="ml", model_name=self.model_name,
                symbol=symbol, direction="NO_TRADE", confidence=0.0,
                reasoning="Model retraining",
            )
        direction_idx = int(np.argmax(prob))
        confidence = float(prob[direction_idx])

        if confidence < self.confidence_threshold:
            direction = "NO_TRADE"
        else:
            direction = "BUY" if direction_idx == 1 else "SELL"

        # SL/TP prediction — mode-aware clamping
        try:
            from .trading_mode import get_current_config
            cfg = get_current_config()
            sl_default = (cfg.sl_min + cfg.sl_max) / 2
            tp_default = (cfg.tp_min + cfg.tp_max) / 2
            sl_min, sl_max = cfg.sl_min, cfg.sl_max
            tp_min, tp_max = cfg.tp_min, cfg.tp_max
        except Exception:
            sl_default, tp_default = 15.0, 30.0
            sl_min, sl_max = 3.0, 50.0
            tp_min, tp_max = 5.0, 150.0

        sl_pips = sl_default
        tp_pips = tp_default

        if self._sl_model is not None:
            try:
                sl_pips = max(sl_min, min(sl_max, float(self._sl_model.predict(feat_array)[0])))
            except Exception:
                pass
        if self._tp_model is not None:
            try:
                tp_pips = max(tp_min, min(tp_max, float(self._tp_model.predict(feat_array)[0])))
            except Exception:
                pass

        signal = TradeSignal(
            model_type="ml",
            model_name=self.model_name,
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            features_used=feat_array.shape[1],
            reasoning=f"XGB prob={prob.tolist()}, SL={sl_pips:.1f}, TP={tp_pips:.1f}",
        )
        self.record_signal(signal)
        return signal

    # ── Persistence ──────────────────────────────────────────────────────

    def save(self, path: Path | None = None) -> None:
        p = path or self._default_path()
        state = {
            "dir_model": self._dir_model,
            "sl_model": self._sl_model,
            "tp_model": self._tp_model,
            "feature_names": self._feature_names,
            "performance": self._performance,
        }
        with open(p, "wb") as f:
            pickle.dump(state, f)
        logger.info("MLTrader saved to %s", p)

    def load(self, path: Path | None = None) -> bool:
        p = path or self._default_path()
        if not p.exists():
            return False
        try:
            with open(p, "rb") as f:
                state = pickle.load(f)
            self._dir_model = state["dir_model"]
            self._sl_model = state.get("sl_model")
            self._tp_model = state.get("tp_model")
            self._feature_names = state.get("feature_names", [])
            self._performance = state.get("performance", self._performance)
            self._is_fitted = self._dir_model is not None
            logger.info("MLTrader loaded from %s", p)
            return True
        except Exception:
            logger.error("Failed to load MLTrader from %s", p, exc_info=True)
            return False
