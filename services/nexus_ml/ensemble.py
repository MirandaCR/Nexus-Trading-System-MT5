"""
Model ensemble — combines all individual model outputs into a single
trading signal with weighted voting, anomaly flagging, and regime context.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

try:
    from .models.xgboost_direction import XGBoostDirectionModel
except ImportError:
    XGBoostDirectionModel = None  # type: ignore[assignment,misc]
try:
    from .models.lightgbm_regime import LightGBMRegimeModel, VolatilityRegime
except ImportError:
    LightGBMRegimeModel = None  # type: ignore[assignment,misc]
    VolatilityRegime = None  # type: ignore[assignment,misc]
try:
    from .models.lstm_sequence import LSTMSequenceModel
except ImportError:
    LSTMSequenceModel = None  # type: ignore[assignment,misc]
try:
    from .models.isolation_anomaly import AnomalyDetector
except ImportError:
    AnomalyDetector = None  # type: ignore[assignment,misc]
try:
    from .models.finbert_sentiment import FinBERTSentiment
except ImportError:
    FinBERTSentiment = None  # type: ignore[assignment,misc]
try:
    from .models.random_forest_features import FeatureImportanceModel
except ImportError:
    FeatureImportanceModel = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)
_AGGRESSIVE_TRADING = os.getenv("NEXUS_AGGRESSIVE_TRADING", "false").lower() == "true"

DEFAULT_WEIGHTS: dict[str, float] = {
    "xgboost": 0.30,
    "lgbm_regime": 0.15,
    "lstm": 0.25,
    "anomaly": 0.10,
    "sentiment": 0.10,
    "rf_importance": 0.10,
}


@dataclass
class EnsembleSignal:
    """Aggregated output from all models."""

    direction: str                       # "BUY" | "SELL" | "NO_TRADE"
    confidence: float                    # weighted average ∈ [0, 1]
    individual_scores: dict[str, float] = field(default_factory=dict)
    regime: str = "MEDIUM"
    regime_confidence: float = 0.0
    anomaly_score: float = 0.0
    is_anomaly: bool = False
    sentiment: dict[str, float] = field(default_factory=dict)
    top_features: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "confidence": round(self.confidence, 4),
            "individual_scores": {k: round(v, 4) for k, v in self.individual_scores.items()},
            "regime": self.regime,
            "regime_confidence": round(self.regime_confidence, 4),
            "anomaly_score": round(self.anomaly_score, 4),
            "is_anomaly": self.is_anomaly,
            "sentiment": {k: round(v, 4) for k, v in self.sentiment.items()},
            "top_features": self.top_features,
        }


class ModelEnsemble:
    """Load and combine all model predictions into a single signal."""

    def __init__(
        self,
        model_dir: str | Path = "./models_store",
        weights: dict[str, float] | None = None,
        confidence_threshold: float = 0.45,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.weights = weights or dict(DEFAULT_WEIGHTS)
        self.confidence_threshold = confidence_threshold

        self.xgb = XGBoostDirectionModel()
        self.lgbm = LightGBMRegimeModel()
        self.lstm = LSTMSequenceModel()
        self.anomaly = AnomalyDetector()
        self.sentiment = FinBERTSentiment()
        self.rf = FeatureImportanceModel()

        self._models_loaded = False
        # Column order the models were actually trained on — persisted
        # alongside the model files since individual model.save()/.load()
        # cycles don't round-trip this (see load_all_models()).
        self.feature_names: list[str] = []

    # ── Feature alignment ─────────────────────────────────────────────────

    def build_feature_array(self, features: dict[str, float]) -> np.ndarray:
        """Build the model input vector aligned to training column order.

        Live feature dicts (from nexus-features) and the dict a model was
        trained on can differ in key set/order — a bare
        ``np.array(list(features.values()))`` silently feeds the wrong
        column into the wrong slot. Missing keys default to 0.0; extra live
        keys not seen during training are dropped.
        """
        if not self.feature_names:
            # No persisted schema (e.g. models never trained this session) —
            # fall back to raw dict order rather than failing outright.
            return np.array(list(features.values()), dtype=np.float64)
        return np.array(
            [float(features.get(name, 0.0) or 0.0) for name in self.feature_names],
            dtype=np.float64,
        )

    # ── Model loading ─────────────────────────────────────────────────────

    def load_all_models(self) -> dict[str, bool]:
        """Attempt to load every model; return a status map."""
        names_path = self.model_dir / "feature_names.json"
        try:
            if names_path.exists():
                self.feature_names = json.loads(names_path.read_text())
                logger.info("Loaded %d training feature names from %s", len(self.feature_names), names_path)
        except Exception:
            logger.warning("Could not load feature_names.json — falling back to raw dict order", exc_info=True)

        status: dict[str, bool] = {}
        loaders = {
            "xgboost": (self.xgb, self.model_dir / "xgboost_direction.json"),
            "lgbm_regime": (self.lgbm, self.model_dir / "lgbm_regime.txt"),
            "lstm": (self.lstm, self.model_dir / "lstm_sequence.pt"),
            "anomaly": (self.anomaly, self.model_dir / "isolation_anomaly.joblib"),
            "rf_importance": (self.rf, self.model_dir / "rf_features.joblib"),
        }

        for name, (model, path) in loaders.items():
            try:
                model.load(path)
                status[name] = True
                logger.info("Loaded %s from %s", name, path)
            except FileNotFoundError:
                status[name] = False
                logger.warning("Model file missing for %s at %s", name, path)
            except Exception:
                status[name] = False
                logger.error("Failed to load %s", name, exc_info=True)

        status["sentiment"] = True  # lazy-loaded on first call
        self._models_loaded = any(status.values())
        return status

    # ── Prediction ────────────────────────────────────────────────────────

    def predict(
        self,
        features: np.ndarray,
        sequence: np.ndarray | None = None,
        headline: str | None = None,
    ) -> EnsembleSignal:
        """Run all available models and combine results.

        Parameters
        ----------
        features : 1-D array of the current bar's flat features.
        sequence : 3-D (1, seq_len, n_features) for the LSTM, or None.
        headline : latest news headline for sentiment scoring, or None.
        """
        votes: dict[str, tuple[int, float]] = {}  # name -> (direction, confidence)

        # XGBoost direction
        if self.xgb.is_fitted:
            try:
                d, c = self.xgb.predict(features)
                votes["xgboost"] = (d, c)
            except Exception:
                logger.warning("XGBoost prediction failed", exc_info=True)

        # LightGBM regime (encode regime as directional hint)
        regime_name = "MEDIUM"
        regime_conf = 0.0
        if self.lgbm.is_fitted:
            try:
                regime, regime_conf = self.lgbm.predict(features)
                regime_name = regime.name
                regime_dir = 1 if regime in (VolatilityRegime.LOW, VolatilityRegime.MEDIUM) else 0
                votes["lgbm_regime"] = (regime_dir, regime_conf)
            except Exception:
                logger.warning("LGBM regime prediction failed", exc_info=True)

        # LSTM sequence
        if self.lstm.is_fitted and sequence is not None:
            try:
                d, c = self.lstm.predict(sequence)
                votes["lstm"] = (d, c)
            except Exception:
                logger.warning("LSTM prediction failed", exc_info=True)

        # Anomaly detection
        anomaly_score = 0.0
        is_anomaly = False
        if self.anomaly.is_fitted:
            try:
                anomaly_score = self.anomaly.detect(features)
                is_anomaly = self.anomaly.is_anomaly(features)
                anomaly_dir = 0 if is_anomaly else 1
                anomaly_conf = min(1.0, max(0.0, 0.5 + anomaly_score))
                votes["anomaly"] = (anomaly_dir, anomaly_conf)
            except Exception:
                logger.warning("Anomaly detection failed", exc_info=True)

        # Sentiment
        sent_scores: dict[str, float] = {}
        if headline:
            try:
                sent_scores = self.sentiment.score_text(headline)
                sent_dir = 1 if sent_scores.get("compound", 0) > 0 else 0
                sent_conf = min(1.0, 0.5 + abs(sent_scores.get("compound", 0)))
                votes["sentiment"] = (sent_dir, sent_conf)
            except Exception:
                logger.warning("Sentiment scoring failed", exc_info=True)

        # RF feature importance (directional prediction)
        top_feats: list[str] = []
        if self.rf.is_fitted:
            try:
                d, c = self.rf.predict(features)
                votes["rf_importance"] = (d, c)
                top_feats = self.rf.select_top_features(10)
            except Exception:
                logger.warning("RF prediction failed", exc_info=True)

        # ── Weighted aggregation ──────────────────────────────────────────

        if not votes:
            return EnsembleSignal(
                direction="NO_TRADE",
                confidence=0.0,
                regime=regime_name,
                regime_confidence=regime_conf,
                anomaly_score=anomaly_score,
                is_anomaly=is_anomaly,
                sentiment=sent_scores,
                top_features=top_feats,
            )

        total_weight_up = 0.0
        total_weight_down = 0.0
        weighted_conf_sum = 0.0
        weight_sum = 0.0
        individual: dict[str, float] = {}

        for name, (direction, conf) in votes.items():
            w = self.weights.get(name, 0.0)
            individual[name] = conf if direction == 1 else -conf
            if direction == 1:
                total_weight_up += w * conf
            else:
                total_weight_down += w * conf
            weighted_conf_sum += w * conf
            weight_sum += w

        avg_confidence = weighted_conf_sum / weight_sum if weight_sum > 0 else 0.0

        if total_weight_up > total_weight_down:
            raw_direction = "BUY"
        elif total_weight_down > total_weight_up:
            raw_direction = "SELL"
        else:
            raw_direction = "NO_TRADE"

        if is_anomaly:
            if _AGGRESSIVE_TRADING:
                # Keep trading flow alive in aggressive mode; degrade confidence only.
                avg_confidence *= 0.8
            else:
                raw_direction = "NO_TRADE"
                avg_confidence *= 0.5
                logger.info("Anomaly detected — overriding to NO_TRADE")

        if avg_confidence < self.confidence_threshold:
            raw_direction = "NO_TRADE"

        return EnsembleSignal(
            direction=raw_direction,
            confidence=avg_confidence,
            individual_scores=individual,
            regime=regime_name,
            regime_confidence=regime_conf,
            anomaly_score=anomaly_score,
            is_anomaly=is_anomaly,
            sentiment=sent_scores,
            top_features=top_feats,
        )

    # ── Status ────────────────────────────────────────────────────────────

    def models_status(self) -> dict[str, bool]:
        return {
            "xgboost": self.xgb.is_fitted,
            "lgbm_regime": self.lgbm.is_fitted,
            "lstm": self.lstm.is_fitted,
            "anomaly": self.anomaly.is_fitted,
            "rf_importance": self.rf.is_fitted,
            "sentiment": self.sentiment.is_loaded,
        }
