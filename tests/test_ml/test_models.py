"""Tests for nexus_ml service — model definitions and ensemble."""
from __future__ import annotations

from pathlib import Path

import pytest


ML_SVC = Path(__file__).resolve().parents[2] / "services" / "nexus_ml"


class TestMLServiceFiles:
    REQUIRED = [
        "__init__.py", "main.py", "backtester.py",
        "ensemble.py", "training.py",
    ]

    @pytest.mark.parametrize("filename", REQUIRED)
    def test_file_exists(self, filename: str):
        assert (ML_SVC / filename).exists(), f"Missing ML file: {filename}"


class TestMLModelFiles:
    MODELS_DIR = ML_SVC / "models"

    REQUIRED_MODELS = [
        "__init__.py",
        "xgboost_direction.py",
        "lightgbm_regime.py",
        "random_forest_features.py",
        "lstm_sequence.py",
        "finbert_sentiment.py",
        "isolation_anomaly.py",
    ]

    @pytest.mark.parametrize("filename", REQUIRED_MODELS)
    def test_model_exists(self, filename: str):
        assert (self.MODELS_DIR / filename).exists(), f"Missing ML model: {filename}"


class TestEnsembleImport:
    def test_importable(self):
        import importlib
        mod = importlib.import_module("services.nexus_ml.ensemble")
        assert mod is not None
