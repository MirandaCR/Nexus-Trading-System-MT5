"""
Isolation Forest anomaly detector.

Flags unusual market conditions — flash crashes, price gaps, unusual
volume spikes — so the trading system can reduce exposure or halt.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest

logger = logging.getLogger(__name__)

DEFAULT_PARAMS: dict[str, Any] = {
    "n_estimators": 200,
    "max_samples": "auto",
    "contamination": 0.05,
    "max_features": 1.0,
    "bootstrap": False,
    "random_state": 42,
    "n_jobs": -1,
}

ANOMALY_FEATURE_COLS = [
    "return_1bar",
    "return_5bar",
    "volume_zscore",
    "spread_zscore",
    "gap_size",
    "atr_ratio",
    "price_velocity",
    "tick_imbalance",
]


class AnomalyDetector:
    """Isolation Forest wrapper for market anomaly detection."""

    def __init__(
        self,
        params: dict[str, Any] | None = None,
        threshold: float = -0.3,
    ) -> None:
        merged = {**DEFAULT_PARAMS, **(params or {})}
        self.model = IsolationForest(**merged)
        self.threshold = threshold
        self._is_fitted = False
        self._feature_names: list[str] = list(ANOMALY_FEATURE_COLS)
        self._baseline_stats: dict[str, float] = {}

    # ── Training ──────────────────────────────────────────────────────────

    def train(
        self,
        normal_data: np.ndarray,
        feature_names: list[str] | None = None,
    ) -> dict[str, float]:
        """Fit on data that represents *normal* market conditions."""
        if feature_names:
            self._feature_names = feature_names

        self.model.fit(normal_data)
        self._is_fitted = True

        scores = self.model.decision_function(normal_data)
        self._baseline_stats = {
            "mean_score": float(np.mean(scores)),
            "std_score": float(np.std(scores)),
            "min_score": float(np.min(scores)),
            "threshold": self.threshold,
            "n_samples": float(len(normal_data)),
        }
        logger.info("AnomalyDetector fitted — baseline stats: %s", self._baseline_stats)
        return self._baseline_stats

    # ── Detection ─────────────────────────────────────────────────────────

    def detect(self, features: np.ndarray) -> float:
        """Return anomaly score.  More negative = more anomalous.

        Typical range: roughly -0.5 (very anomalous) to +0.5 (normal).
        """
        if not self._is_fitted:
            raise RuntimeError("Model not fitted — call train() first")
        if features.ndim == 1:
            features = features.reshape(1, -1)
        return float(self.model.decision_function(features)[0])

    def is_anomaly(self, features: np.ndarray) -> bool:
        return self.detect(features) < self.threshold

    def detect_batch(self, X: np.ndarray) -> np.ndarray:
        if not self._is_fitted:
            raise RuntimeError("Model not fitted — call train() first")
        return self.model.decision_function(X)

    def flag_anomalies(self, X: np.ndarray) -> list[dict[str, Any]]:
        """Return list of anomaly records for rows that exceed threshold."""
        scores = self.detect_batch(X)
        anomalies = []
        for idx, score in enumerate(scores):
            if score < self.threshold:
                anomalies.append({
                    "index": idx,
                    "score": float(score),
                    "severity": self._severity(score),
                })
        return anomalies

    def _severity(self, score: float) -> str:
        if score < -0.5:
            return "CRITICAL"
        if score < -0.4:
            return "HIGH"
        if score < self.threshold:
            return "MODERATE"
        return "LOW"

    # ── Persistence ───────────────────────────────────────────────────────

    def save(self, path: str | Path, mlflow_run: bool = True) -> str:
        import joblib

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model": self.model,
            "threshold": self.threshold,
            "feature_names": self._feature_names,
            "baseline_stats": self._baseline_stats,
        }
        joblib.dump(payload, str(path))
        logger.info("AnomalyDetector saved to %s", path)

        if mlflow_run:
            try:
                import mlflow
                mlflow.log_artifact(str(path), "isolation_anomaly")
                mlflow.log_metrics(self._baseline_stats)
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
        self.threshold = payload["threshold"]
        self._feature_names = payload["feature_names"]
        self._baseline_stats = payload["baseline_stats"]
        self._is_fitted = True
        logger.info("AnomalyDetector loaded from %s", path)

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    @property
    def baseline_stats(self) -> dict[str, float]:
        return dict(self._baseline_stats)
