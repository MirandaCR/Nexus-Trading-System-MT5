"""Tests for the BaseAgent abstract class."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from shared.redis_client import RedisClient
from services.nexus_agents.agents.base_agent import BaseAgent
from services.nexus_agents.state import AgentState


class ConcreteAgent(BaseAgent):
    """Minimal concrete implementation for testing the ABC."""

    async def invoke(self, state: AgentState) -> AgentState:
        state["technical_analysis"] = "test output"
        return state


class TestBaseAgent:
    @pytest.fixture()
    def agent(self, redis_client):
        return ConcreteAgent("A1", "Test Agent", redis_client=redis_client)

    def test_init(self, agent):
        assert agent.agent_id == "A1"
        assert agent.name == "Test Agent"
        assert agent.color.startswith("#")

    @pytest.mark.asyncio
    async def test_invoke(self, agent, make_agent_state):
        state = make_agent_state()
        result = await agent.invoke(state)
        assert result["technical_analysis"] == "test output"

    @pytest.mark.asyncio
    async def test_safe_invoke_catches_error(self, redis_client, make_agent_state):
        class FailingAgent(BaseAgent):
            async def invoke(self, state: AgentState) -> AgentState:
                raise ValueError("boom")

        agent = FailingAgent("A1", "Failing", redis_client=redis_client)
        state = make_agent_state()
        result = await agent.safe_invoke(state)
        assert any("boom" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_publish_message_no_redis(self, make_agent_state):
        agent = ConcreteAgent("A1", "Test", redis_client=None)
        state = make_agent_state()
        await agent._publish_message("test", state=state)

    def test_load_prompt_fallback(self, redis_client):
        agent = ConcreteAgent("A99", "Nonexistent", redis_client=redis_client)
        prompt = agent._load_prompt("nonexistent_prompt_xyz")
        assert prompt is not None

    @pytest.mark.asyncio
    async def test_log_reasoning(self, redis_client, make_agent_state):
        agent = ConcreteAgent("A1", "Test", redis_client=redis_client)
        state = make_agent_state()
        await agent._log_reasoning("Test reasoning string", state=state)
