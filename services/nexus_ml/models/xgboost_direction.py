"""
XGBoost price-direction classifier.

Predicts whether the next bar closes UP or DOWN using gradient-boosted trees
with hyperparameters tuned for noisy financial time-series data.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import xgboost as xgb
from sklearn.metrics import accuracy_score, f1_score, log_loss
from sklearn.model_selection import TimeSeriesSplit

logger = logging.getLogger(__name__)

DEFAULT_PARAMS: dict[str, Any] = {
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "max_depth": 4,
    "learning_rate": 0.02,
    "n_estimators": 800,
    "subsample": 0.8,
    "colsample_bytree": 0.6,
    "colsample_bylevel": 0.6,
    "min_child_weight": 5,
    "gamma": 1.0,
    "reg_alpha": 0.1,
    "reg_lambda": 1.5,
    "scale_pos_weight": 1.0,
    "max_delta_step": 1,
    "tree_method": "hist",
    "random_state": 42,
    "n_jobs": -1,
    "early_stopping_rounds": 50,
}


class XGBoostDirectionModel:
    """Binary classifier: UP (1) vs DOWN (0) for next-bar direction."""

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        merged = {**DEFAULT_PARAMS, **(params or {})}
        early_stop = merged.pop("early_stopping_rounds", 50)
        self._early_stopping_rounds = early_stop
        self.model = xgb.XGBClassifier(**merged)
        # xgboost 2.1.x / scikit-learn compat gap: XGBClassifier doesn't always
        # resolve `_estimator_type` via its ClassifierMixin, which crashes
        # save_model()'s metadata serialization with "`_estimator_type`
        # undefined" — set it explicitly so persistence works.
        self.model._estimator_type = "classifier"
        self._feature_names: list[str] = []
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
        """Walk-forward cross-validated training.

        Returns aggregate metrics across all folds.
        """
        if params:
            early_stop = params.pop("early_stopping_rounds", self._early_stopping_rounds)
            self._early_stopping_rounds = early_stop
            self.model.set_params(**params)

        if feature_names:
            self._feature_names = feature_names

        tscv = TimeSeriesSplit(n_splits=n_splits)
        fold_metrics: list[dict[str, float]] = []

        for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
            X_train, X_val = X[train_idx], X[val_idx]
            y_train, y_val = y[train_idx], y[val_idx]

            self.model.fit(
                X_train,
                y_train,
                eval_set=[(X_val, y_val)],
                verbose=False,
            )

            y_pred = self.model.predict(X_val)
            y_prob = self.model.predict_proba(X_val)[:, 1]

            metrics = {
                "accuracy": accuracy_score(y_val, y_pred),
                "f1": f1_score(y_val, y_pred, zero_division=0),
                "log_loss": log_loss(y_val, y_prob, labels=[0, 1]),
            }
            fold_metrics.append(metrics)
            logger.info("Fold %d/%d — acc=%.4f  f1=%.4f  logloss=%.4f",
                        fold + 1, n_splits, metrics["accuracy"],
                        metrics["f1"], metrics["log_loss"])

        self._train_metrics = {
            k: float(np.mean([m[k] for m in fold_metrics]))
            for k in fold_metrics[0]
        }

        self.model.fit(
            X, y,
            eval_set=[(X, y)],
            verbose=False,
        )
        self._is_fitted = True

        logger.info("Final train metrics: %s", self._train_metrics)
        return self._train_metrics

    # ── Prediction ────────────────────────────────────────────────────────

    def predict(self, features: np.ndarray) -> tuple[int, float]:
        """Return (direction, probability).

        direction: 1 = UP, 0 = DOWN
        probability: model confidence in the predicted direction
        """
        if not self._is_fitted:
            raise RuntimeError("Model not fitted — call train() first")

        if features.ndim == 1:
            features = features.reshape(1, -1)

        proba = self.model.predict_proba(features)[:, 1]
        direction = int(proba[0] >= 0.5)
        confidence = float(proba[0]) if direction == 1 else float(1.0 - proba[0])
        return direction, confidence

    def predict_batch(self, X: np.ndarray) -> list[tuple[int, float]]:
        if not self._is_fitted:
            raise RuntimeError("Model not fitted — call train() first")
        proba = self.model.predict_proba(X)[:, 1]
        results = []
        for p in proba:
            direction = int(p >= 0.5)
            conf = float(p) if direction == 1 else float(1.0 - p)
            results.append((direction, conf))
        return results

    # ── Feature importance ────────────────────────────────────────────────

    def feature_importance(self) -> dict[str, float]:
        if not self._is_fitted:
            return {}
        raw = self.model.feature_importances_
        names = self._feature_names or [f"f{i}" for i in range(len(raw))]
        return dict(sorted(zip(names, map(float, raw)),
                           key=lambda x: x[1], reverse=True))

    # ── Persistence via MLflow ────────────────────────────────────────────

    def save(self, path: str | Path, mlflow_run: bool = True) -> str:
        """Save model to disk and optionally log to MLflow."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.model.save_model(str(path))
        logger.info("Model saved to %s", path)

        if mlflow_run:
            try:
                import mlflow
                import mlflow.xgboost

                mlflow.xgboost.log_model(self.model, "xgboost_direction")
                mlflow.log_metrics(self._train_metrics)
                mlflow.log_params({
                    "max_depth": self.model.get_params().get("max_depth"),
                    "learning_rate": self.model.get_params().get("learning_rate"),
                    "n_estimators": self.model.get_params().get("n_estimators"),
                })
                logger.info("Model logged to MLflow")
            except Exception:
                logger.warning("MLflow logging failed — model saved locally only", exc_info=True)

        return str(path)

    def load(self, path: str | Path) -> None:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"No model file at {path}")
        self.model.load_model(str(path))
        self._is_fitted = True
        logger.info("Model loaded from %s", path)

    @classmethod
    def load_from_mlflow(cls, model_uri: str) -> "XGBoostDirectionModel":
        import mlflow.xgboost

        raw_model = mlflow.xgboost.load_model(model_uri)
        instance = cls()
        instance.model = raw_model
        instance._is_fitted = True
        return instance

    # ── Internals ─────────────────────────────────────────────────────────

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    @property
    def train_metrics(self) -> dict[str, float]:
        return dict(self._train_metrics)
