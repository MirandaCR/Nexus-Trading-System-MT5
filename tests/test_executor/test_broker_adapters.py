"""Tests for nexus_executor service — broker adapters."""
from __future__ import annotations

from pathlib import Path

import pytest


EXEC_SVC = Path(__file__).resolve().parents[2] / "services" / "nexus_executor"


class TestExecutorServiceFiles:
    REQUIRED = [
        "__init__.py", "main.py", "broker_adapter.py",
        "mt5_executor.py", "ibkr_executor.py",
        "ccxt_executor.py", "smart_routing.py",
    ]

    @pytest.mark.parametrize("filename", REQUIRED)
    def test_file_exists(self, filename: str):
        assert (EXEC_SVC / filename).exists(), f"Missing executor file: {filename}"


class TestBrokerAdapterImport:
    def test_importable(self):
        import importlib
        mod = importlib.import_module("services.nexus_executor.broker_adapter")
        assert mod is not None


class TestSmartRoutingImport:
    def test_importable(self):
        import importlib
        mod = importlib.import_module("services.nexus_executor.smart_routing")
        assert mod is not None
