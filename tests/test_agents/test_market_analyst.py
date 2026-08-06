"""Tests for A1 — MarketAnalystAgent."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from shared.redis_client import RedisClient
from services.nexus_agents.agents.market_analyst import MarketAnalystAgent


class TestMarketAnalystAgent:
    @pytest.fixture()
    def agent(self, redis_client: RedisClient):
        with patch("services.nexus_agents.agents.base_agent._load_agent_configs", return_value={}):
            return MarketAnalystAgent(redis_client=redis_client)

    def test_agent_id(self, agent):
        assert agent.agent_id == "A1"
        assert agent.name == "Market Analyst"

    @pytest.mark.asyncio
    async def test_invoke_with_mocked_tools(self, agent, make_agent_state):
        state = make_agent_state()

        with (
            patch("services.nexus_agents.agents.market_analyst.get_indicators", new_callable=AsyncMock) as mock_ind,
            patch("services.nexus_agents.agents.market_analyst.query_duckdb", new_callable=AsyncMock) as mock_db,
            patch("services.nexus_agents.agents.market_analyst.calculate_sr", new_callable=AsyncMock) as mock_sr,
            patch("services.nexus_agents.agents.market_analyst.detect_patterns", new_callable=AsyncMock) as mock_pat,
        ):
            mock_ind.return_value = {"rsi": 55.0, "ema_20": 1.085, "ema_50": 1.083}
            mock_db.return_value = [{"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05}]
            mock_sr.return_value = {"support": [1.08], "resistance": [1.09]}
            mock_pat.return_value = ["bullish_engulfing"]

            with patch.object(agent, "_get_llm") as mock_llm:
                mock_response = AsyncMock()
                mock_response.content = "Strong uptrend on M15 with RSI at 55"
                mock_chain = AsyncMock()
                mock_chain.ainvoke = AsyncMock(return_value=mock_response)
                mock_llm.return_value.__or__ = lambda self, other: mock_chain

                result = await agent.invoke(state)

            assert result["technical_analysis"] != ""
            assert isinstance(result["messages"], list)

    @pytest.mark.asyncio
    async def test_invoke_survives_indicator_failure(self, agent, make_agent_state):
        state = make_agent_state()

        with (
            patch("services.nexus_agents.agents.market_analyst.get_indicators", new_callable=AsyncMock, side_effect=Exception("service down")),
            patch("services.nexus_agents.agents.market_analyst.query_duckdb", new_callable=AsyncMock, return_value=[]),
        ):
            with patch.object(agent, "_get_llm", side_effect=Exception("LLM down")):
                result = await agent.invoke(state)

            assert result["technical_analysis"] != ""
            assert len(result["errors"]) > 0
