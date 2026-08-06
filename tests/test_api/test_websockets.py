"""Tests for nexus_api WebSocket endpoints."""
from __future__ import annotations

from pathlib import Path

import pytest


class TestWebSocketModulesExist:
    """Ensure every required WS module is present and importable."""

    WS_DIR = Path(__file__).resolve().parents[2] / "services" / "nexus_api" / "websockets"

    REQUIRED = ["ticks.py", "agents.py", "positions.py", "orders.py", "news.py", "risk.py"]

    @pytest.mark.parametrize("filename", REQUIRED)
    def test_ws_module_exists(self, filename: str):
        assert (self.WS_DIR / filename).exists(), f"Missing WS module: {filename}"

    def test_manager_exists(self):
        assert (self.WS_DIR / "manager.py").exists()

    def test_init_exists(self):
        assert (self.WS_DIR / "__init__.py").exists()
