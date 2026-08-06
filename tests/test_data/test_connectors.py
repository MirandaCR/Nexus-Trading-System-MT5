"""Tests for nexus_data service connectors."""
from __future__ import annotations

from pathlib import Path

import pytest


DATA_SVC = Path(__file__).resolve().parents[2] / "services" / "nexus_data"


class TestDataServiceFiles:
    REQUIRED = [
        "__init__.py", "main.py", "mt5_connector.py",
        "ccxt_connector.py", "ibkr_connector.py",
        "yfinance_connector.py", "duckdb_store.py",
        "redis_cache.py",
    ]

    @pytest.mark.parametrize("filename", REQUIRED)
    def test_file_exists(self, filename: str):
        assert (DATA_SVC / filename).exists(), f"Missing data file: {filename}"


class TestDuckDBStore:
    def test_importable(self):
        from services.nexus_data.duckdb_store import DuckDBStore  # noqa: F401


class TestRedisCache:
    def test_importable(self):
        from services.nexus_data.redis_cache import RedisCache  # noqa: F401
