"""
Random Forest–based feature importance ranker.

Trains a quick RF classifier to identify the most predictive features in
the current feature set, enabling dynamic feature selection for the heavier
models (XGBoost, LSTM).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.model_selection import TimeSeriesSplit

logger = logging.getLogger(__name__)

DEFAULT_PARAMS: dict[str, Any] = {
    "n_estimators": 300,
    "max_depth": 6,
    "min_samples_leaf": 10,
    "max_features": "sqrt",
    "class_weight": "balanced",
    "random_state": 42,
    "n_jobs": -1,
}


class FeatureImportanceModel:
    """Trains a Random Forest mainly to rank feature importance."""

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        merged = {**DEFAULT_PARAMS, **(params or {})}
        self.model = RandomForestClassifier(**merged)
        self._feature_names: list[str] = []
        self._importance_scores: dict[str, float] = {}
        self._permutation_scores: dict[str, float] = {}
        self._is_fitted = False
        self._train_metrics: dict[str, float] = {}

    # ── Training ──────────────────────────────────────────────────────────

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: list[str] | None = None,
        compute_permutation: bool = True,
    ) -> dict[str, float]:
        if feature_names:
            self._feature_names = feature_names
        else:
            self._feature_names = [f"f{i}" for i in range(X.shape[1])]

        tscv = TimeSeriesSplit(n_splits=3)
        accs: list[float] = []
        for train_idx, val_idx in tscv.split(X):
            self.model.fit(X[train_idx], y[train_idx])
            accs.append(float(self.model.score(X[val_idx], y[val_idx])))

        self.model.fit(X, y)
        self._is_fitted = True

        raw_imp = self.model.feature_importances_
        self._importance_scores = dict(
            sorted(
                zip(self._feature_names, map(float, raw_imp)),
                key=lambda x: x[1],
                reverse=True,
            )
        )

        if compute_permutation:
            split = max(1, int(len(X) * 0.8))
            X_val, y_val = X[split:], y[split:]
            if len(X_val) > 50:
                perm = permutation_importance(
                    self.model, X_val, y_val,
                    n_repeats=10, random_state=42, n_jobs=-1,
                )
                self._permutation_scores = dict(
                    sorted(
                        zip(self._feature_names, map(float, perm.importances_mean)),
                        key=lambda x: x[1],
                        reverse=True,
                    )
                )

        self._train_metrics = {
            "cv_accuracy": float(np.mean(accs)),
            "n_features": float(X.shape[1]),
        }
        logger.info("FeatureImportance trained — cv_acc=%.4f  top3=%s",
                     self._train_metrics["cv_accuracy"],
                     list(self._importance_scores.keys())[:3])
        return self._train_metrics

    # ── Importance query ──────────────────────────────────────────────────

    def get_importance_ranking(self, use_permutation: bool = False) -> dict[str, float]:
        source = self._permutation_scores if (use_permutation and self._permutation_scores) else self._importance_scores
        return dict(source)

    def select_top_features(self, n: int = 15, use_permutation: bool = False) -> list[str]:
        ranking = self.get_importance_ranking(use_permutation)
        return list(ranking.keys())[:n]

    def get_feature_mask(self, n: int = 15) -> np.ndarray:
        """Boolean mask for the top-N features (by index in _feature_names)."""
        top = set(self.select_top_features(n))
        return np.array([name in top for name in self._feature_names])

    # ── Persistence ───────────────────────────────────────────────────────

    def save(self, path: str | Path, mlflow_run: bool = True) -> str:
        import joblib

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model": self.model,
            "feature_names": self._feature_names,
            "importance_scores": self._importance_scores,
            "permutation_scores": self._permutation_scores,
            "train_metrics": self._train_metrics,
        }
        joblib.dump(payload, str(path))
        logger.info("FeatureImportanceModel saved to %s", path)

        if mlflow_run:
            try:
                import mlflow
                mlflow.log_artifact(str(path), "rf_feature_importance")
                mlflow.log_metrics(self._train_metrics)
            except Exception:
                logger.warning("MLflow logging failed", exc_info=True)

        return str(path)

    def load(self, path: str | Path) -> None:
        import joblib

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"No model at {path}")
        payload = joblib.load(str(path))
        self.model = payload["model"]
        self._feature_names = payload["feature_names"]
        self._importance_scores = payload["importance_scores"]
        self._permutation_scores = payload["permutation_scores"]
        self._train_metrics = payload.get("train_metrics", {})
        self._is_fitted = True
        logger.info("FeatureImportanceModel loaded from %s", path)

    # ── Predict (lightweight — mainly used for feature selection) ─────────

    def predict(self, features: np.ndarray) -> tuple[int, float]:
        """Predict direction; secondary to the importance-ranking purpose."""
        if not self._is_fitted:
            raise RuntimeError("Model not fitted")
        if features.ndim == 1:
            features = features.reshape(1, -1)
        proba = self.model.predict_proba(features)[0]
        direction = int(np.argmax(proba))
        return direction, float(proba[direction])

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    @property
    def train_metrics(self) -> dict[str, float]:
        return dict(self._train_metrics)
