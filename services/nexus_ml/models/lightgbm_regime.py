"""
LightGBM volatility-regime classifier.

Classifies market conditions into four volatility regimes so downstream
models and risk sizing can adapt dynamically.
"""
from __future__ import annotations

import logging
from enum import IntEnum
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.model_selection import TimeSeriesSplit

logger = logging.getLogger(__name__)


class VolatilityRegime(IntEnum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2
    EXTREME = 3


REGIME_LABELS = {v.value: v.name for v in VolatilityRegime}

REGIME_FEATURE_COLS = [
    "atr_percentile",
    "bb_width",
    "realized_vol_20",
    "realized_vol_60",
    "atr_ratio",
    "range_pct",
    "vol_of_vol",
    "garman_klass_vol",
    "parkinson_vol",
    "volume_zscore",
]

DEFAULT_PARAMS: dict[str, Any] = {
    "objective": "multiclass",
    "num_class": 4,
    "metric": "multi_logloss",
    "boosting_type": "gbdt",
    "num_leaves": 31,
    "learning_rate": 0.03,
    "n_estimators": 600,
    "subsample": 0.8,
    "colsample_bytree": 0.7,
    "min_child_samples": 20,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "class_weight": "balanced",
    "random_state": 42,
    "n_jobs": -1,
    "verbose": -1,
}


class LightGBMRegimeModel:
    """Multi-class classifier for volatility regime detection."""

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        merged = {**DEFAULT_PARAMS, **(params or {})}
        self.model = lgb.LGBMClassifier(**merged)
        self._feature_names: list[str] = list(REGIME_FEATURE_COLS)
        self._is_fitted = False
        self._train_metrics: dict[str, float] = {}

    # ── Training ──────────────────────────────────────────────────────────

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        params: dict[str, Any] | None = None,
        feature_names: list[str] | None = None,
        n_splits: int = 5,
    ) -> dict[str, float]:
        if params:
            self.model.set_params(**params)
        if feature_names:
            self._feature_names = feature_names

        tscv = TimeSeriesSplit(n_splits=n_splits)
        fold_accs: list[float] = []
        fold_f1s: list[float] = []

        for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
            X_train, X_val = X[train_idx], X[val_idx]
            y_train, y_val = y[train_idx], y[val_idx]

            # A time-ordered split can leave a regime class (e.g. a rare
            # HIGH_VOLATILITY stretch) entirely out of the train slice while
            # it appears in validation — LightGBM's internal label encoder
            # then raises on eval_set instead of just scoring it wrong. Drop
            # eval_set for that fold rather than losing the whole run.
            if set(np.unique(y_val)) - set(np.unique(y_train)):
                logger.warning(
                    "Fold %d: validation has regime classes unseen in "
                    "training — fitting without eval_set", fold + 1,
                )
                self.model.fit(X_train, y_train)
            else:
                self.model.fit(
                    X_train, y_train,
                    eval_set=[(X_val, y_val)],
                    callbacks=[lgb.log_evaluation(period=0)],
                )

            y_pred = self.model.predict(X_val)
            acc = accuracy_score(y_val, y_pred)
            f1 = f1_score(y_val, y_pred, average="weighted", zero_division=0)
            fold_accs.append(acc)
            fold_f1s.append(f1)
            logger.info("Fold %d/%d — acc=%.4f  f1_weighted=%.4f", fold + 1, n_splits, acc, f1)

        self._train_metrics = {
            "accuracy": float(np.mean(fold_accs)),
            "f1_weighted": float(np.mean(fold_f1s)),
        }

        self.model.fit(X, y)
        self._is_fitted = True

        logger.info("Final regime metrics: %s", self._train_metrics)
        return self._train_metrics

    # ── Prediction ────────────────────────────────────────────────────────

    def predict(self, features: np.ndarray) -> tuple[VolatilityRegime, float]:
        """Return (regime, confidence)."""
        if not self._is_fitted:
            raise RuntimeError("Model not fitted — call train() first")

        if features.ndim == 1:
            features = features.reshape(1, -1)

        proba = self.model.predict_proba(features)[0]
        regime_idx = int(np.argmax(proba))
        confidence = float(proba[regime_idx])
        return VolatilityRegime(regime_idx), confidence

    def predict_regime_name(self, features: np.ndarray) -> tuple[str, float]:
        regime, conf = self.predict(features)
        return regime.name, conf

    def predict_batch(self, X: np.ndarray) -> list[tuple[VolatilityRegime, float]]:
        if not self._is_fitted:
            raise RuntimeError("Model not fitted — call train() first")
        proba = self.model.predict_proba(X)
        results = []
        for row in proba:
            idx = int(np.argmax(row))
            results.append((VolatilityRegime(idx), float(row[idx])))
        return results

    # ── Feature importance ────────────────────────────────────────────────

    def feature_importance(self) -> dict[str, float]:
        if not self._is_fitted:
            return {}
        raw = self.model.feature_importances_
        names = self._feature_names or [f"f{i}" for i in range(len(raw))]
        total = float(raw.sum()) or 1.0
        normalised = raw / total
        return dict(sorted(zip(names, map(float, normalised)),
                           key=lambda x: x[1], reverse=True))

    # ── Persistence ───────────────────────────────────────────────────────

    def save(self, path: str | Path, mlflow_run: bool = True) -> str:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.model.booster_.save_model(str(path))
        logger.info("Regime model saved to %s", path)

        if mlflow_run:
            try:
                import mlflow
                import mlflow.lightgbm

                mlflow.lightgbm.log_model(self.model, "lgbm_regime")
                mlflow.log_metrics(self._train_metrics)
                logger.info("Regime model logged to MLflow")
            except Exception:
                logger.warning("MLflow logging failed", exc_info=True)

        return str(path)

    def load(self, path: str | Path) -> None:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"No model file at {path}")
        booster = lgb.Booster(model_file=str(path))
        self.model._Booster = booster
        self.model._n_classes = 4
        self._is_fitted = True
        logger.info("Regime model loaded from %s", path)

    @classmethod
    def load_from_mlflow(cls, model_uri: str) -> "LightGBMRegimeModel":
        import mlflow.lightgbm

        raw = mlflow.lightgbm.load_model(model_uri)
        instance = cls()
        instance.model = raw
        instance._is_fitted = True
        return instance

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def label_regimes(atr_percentile: np.ndarray) -> np.ndarray:
        """Quantile-based regime labelling for training targets."""
        labels = np.zeros(len(atr_percentile), dtype=int)
        labels[atr_percentile >= 0.25] = VolatilityRegime.MEDIUM
        labels[atr_percentile >= 0.65] = VolatilityRegime.HIGH
        labels[atr_percentile >= 0.90] = VolatilityRegime.EXTREME
        return labels

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    @property
    def train_metrics(self) -> dict[str, float]:
        return dict(self._train_metrics)
