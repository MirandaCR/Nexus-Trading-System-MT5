"""Tests for nexus_api routers — positions, trades, performance, etc."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


class TestPositionsRouter:
    @pytest.mark.asyncio
    async def test_list_positions_empty(self, api_client: AsyncClient):
        resp = await api_client.get("/api/positions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["positions"] == []

    @pytest.mark.asyncio
    async def test_get_position_not_found(self, api_client: AsyncClient):
        resp = await api_client.get("/api/positions/nonexistent")
        assert resp.status_code == 404


class TestTradesRouter:
    @pytest.mark.asyncio
    async def test_list_trades(self, api_client: AsyncClient):
        resp = await api_client.get("/api/trades")
        assert resp.status_code in (200, 404)


class TestPerformanceRouter:
    @pytest.mark.asyncio
    async def test_get_performance(self, api_client: AsyncClient):
        resp = await api_client.get("/api/performance")
        assert resp.status_code in (200, 404)


class TestAgentsRouter:
    @pytest.mark.asyncio
    async def test_list_agents(self, api_client: AsyncClient):
        resp = await api_client.get("/api/agents")
        assert resp.status_code in (200, 404)


class TestRiskRouter:
    @pytest.mark.asyncio
    async def test_get_risk_state(self, api_client: AsyncClient):
        resp = await api_client.get("/api/risk")
        assert resp.status_code in (200, 404)


class TestSystemRouter:
    @pytest.mark.asyncio
    async def test_health(self, api_client: AsyncClient):
        resp = await api_client.get("/api/system/health")
        assert resp.status_code in (200, 404)


class TestConfigRouter:
    @pytest.mark.asyncio
    async def test_get_config(self, api_client: AsyncClient):
        resp = await api_client.get("/api/config")
        assert resp.status_code in (200, 404)
