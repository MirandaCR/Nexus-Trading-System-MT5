"""Tests for nexus_risk service — rules engine and position sizer."""
from __future__ import annotations

from pathlib import Path

import pytest


RISK_SVC = Path(__file__).resolve().parents[2] / "services" / "nexus_risk"


class TestRiskServiceFiles:
    REQUIRED = [
        "__init__.py", "main.py", "rules_engine.py",
        "position_sizer.py", "anti_tilt.py",
        "correlation_checker.py",
    ]

    @pytest.mark.parametrize("filename", REQUIRED)
    def test_file_exists(self, filename: str):
        assert (RISK_SVC / filename).exists(), f"Missing risk file: {filename}"


class TestRiskRulesConstants:
    def test_default_params_sane(self):
        from shared.constants import DEFAULT_RISK_PARAMS

        assert DEFAULT_RISK_PARAMS["max_risk_per_trade_pct"] <= 5.0
        assert DEFAULT_RISK_PARAMS["max_open_positions"] >= 1
        assert DEFAULT_RISK_PARAMS["min_rr_ratio"] >= 1.0
        assert DEFAULT_RISK_PARAMS["max_spread_pips"] > 0


class TestPositionSizerImport:
    def test_importable(self):
        import importlib
        mod = importlib.import_module("services.nexus_risk.position_sizer")
        assert mod is not None


class TestAntiTiltImport:
    def test_importable(self):
        import importlib
        mod = importlib.import_module("services.nexus_risk.anti_tilt")
        assert mod is not None
