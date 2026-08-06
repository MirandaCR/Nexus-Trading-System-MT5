"""
Shared FastAPI dependency providers.

Every router imports from here instead of ``main`` to avoid circular imports.
The singletons are populated by the lifespan in ``main.py``.
"""
from __future__ import annotations

from shared.config_loader import ConfigLoader
from shared.duckdb_client import DuckDBClient
from shared.redis_client import RedisClient

from .websockets.manager import ConnectionManager

redis: RedisClient = RedisClient()
duckdb: DuckDBClient = DuckDBClient(read_only=True)
config_loader: ConfigLoader = ConfigLoader()
ws_manager: ConnectionManager = ConnectionManager()


def get_redis() -> RedisClient:
    return redis


def get_duckdb() -> DuckDBClient:
    return duckdb


def get_config() -> ConfigLoader:
    return config_loader


def get_ws_manager() -> ConnectionManager:
    return ws_manager
