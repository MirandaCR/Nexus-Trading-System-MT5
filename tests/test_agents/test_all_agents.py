"""Parametric smoke tests ensuring every agent A1-A12 can be instantiated."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from shared.redis_client import RedisClient


AGENT_CLASSES = [
    ("services.nexus_agents.agents.market_analyst", "MarketAnalystAgent", "A1"),
    ("services.nexus_agents.agents.news_sentinel", "NewsSentinelAgent", "A2"),
    ("services.nexus_agents.agents.intermarket", "IntermarketAgent", "A3"),
    ("services.nexus_agents.agents.order_flow", "OrderFlowAgent", "A4"),
    ("services.nexus_agents.agents.bull_researcher", "BullResearcherAgent", "A5"),
    ("services.nexus_agents.agents.bear_researcher", "BearResearcherAgent", "A6"),
    ("services.nexus_agents.agents.arbitrator", "ArbitratorAgent", "A7"),
    ("services.nexus_agents.agents.risk_manager", "RiskManagerAgent", "A8"),
    ("services.nexus_agents.agents.trade_executor", "TradeExecutorAgent", "A9"),
    ("services.nexus_agents.agents.position_manager", "PositionManagerAgent", "A10"),
    ("services.nexus_agents.agents.session_governor", "SessionGovernorAgent", "A11"),
    ("services.nexus_agents.agents.portfolio_strategist", "PortfolioStrategistAgent", "A12"),
]


@pytest.mark.parametrize("module_path,class_name,expected_id", AGENT_CLASSES)
def test_agent_instantiation(module_path: str, class_name: str, expected_id: str, redis_client: RedisClient):
    """Every agent class must be importable and constructable."""
    import importlib

    with patch("services.nexus_agents.agents.base_agent._load_agent_configs", return_value={}):
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        agent = cls(redis_client=redis_client)
        assert agent.agent_id == expected_id
        assert hasattr(agent, "invoke")
        assert hasattr(agent, "safe_invoke")


@pytest.mark.parametrize("module_path,class_name,expected_id", AGENT_CLASSES)
def test_agent_has_prompt_yaml(module_path: str, class_name: str, expected_id: str):
    """Every agent should have a corresponding YAML prompt file."""
    from pathlib import Path

    prompts_dir = Path(__file__).resolve().parents[2] / "services" / "nexus_agents" / "prompts"
    name_map = {
        "A1": "market_analyst", "A2": "news_sentinel", "A3": "intermarket",
        "A4": "order_flow", "A5": "bull_researcher", "A6": "bear_researcher",
        "A7": "arbitrator", "A8": "risk_manager", "A9": "trade_executor",
        "A10": "position_manager", "A11": "session_governor", "A12": "portfolio_strategist",
    }
    yaml_file = prompts_dir / f"{name_map[expected_id]}.yaml"
    assert yaml_file.exists(), f"Missing prompt: {yaml_file}"
