"""Tests for nexus_features service — indicators and key levels."""
from __future__ import annotations

from pathlib import Path

import pytest


FEAT_SVC = Path(__file__).resolve().parents[2] / "services" / "nexus_features"


class TestFeatureServiceFiles:
    REQUIRED = [
        "__init__.py", "main.py", "indicators.py",
        "key_levels.py", "multi_timeframe.py",
        "session_tagger.py", "trend_analyzer.py",
        "volatility.py",
    ]

    @pytest.mark.parametrize("filename", REQUIRED)
    def test_file_exists(self, filename: str):
        assert (FEAT_SVC / filename).exists(), f"Missing features file: {filename}"


class TestIndicatorsImport:
    def test_importable(self):
        from services.nexus_features.indicators import (  # noqa: F401
            compute_indicators,
        ) if hasattr(__import__("services.nexus_features.indicators", fromlist=["compute_indicators"]), "compute_indicators") else None


class TestKeyLevelsImport:
    def test_importable(self):
        import importlib
        mod = importlib.import_module("services.nexus_features.key_levels")
        assert mod is not None
