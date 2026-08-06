"""Tests for the AgentState TypedDict contract."""
from __future__ import annotations

from typing import get_type_hints

from services.nexus_agents.state import AgentState


class TestAgentState:
    def test_required_fields(self):
        hints = get_type_hints(AgentState)
        required = [
            "symbol", "timeframe", "session",
            "ml_signal", "features",
            "technical_analysis", "news_assessment",
            "intermarket_score", "flow_score",
            "bull_case", "bull_conviction",
            "bear_case", "bear_conviction",
            "verdict", "verdict_conviction", "verdict_reasoning",
            "risk_decision", "position_size", "risk_reasoning",
            "order", "messages", "cycle_id", "errors",
        ]
        for field in required:
            assert field in hints, f"Missing field: {field}"

    def test_factory_produces_valid_state(self, make_agent_state):
        state = make_agent_state()
        assert state["symbol"] == "EURUSD"
        assert state["session"] == "LONDON"
        assert state["verdict"] == "NO_TRADE"
        assert isinstance(state["messages"], list)
        assert isinstance(state["errors"], list)

    def test_factory_overrides(self, make_agent_state):
        state = make_agent_state(symbol="GBPUSD", session="TOKYO", verdict="BUY", verdict_conviction=85)
        assert state["symbol"] == "GBPUSD"
        assert state["verdict"] == "BUY"
        assert state["verdict_conviction"] == 85
