"""Tests for shared.events — event schemas and the parse/registry logic."""
from __future__ import annotations

from shared.events import (
    EVENT_CLASS_MAP,
    STREAM_FOR_EVENT,
    AgentAnalysisEvent,
    AgentVerdictEvent,
    Event,
    EventType,
    FeatureEvent,
    NewsAlertEvent,
    OHLCVEvent,
    OrderEvent,
    PositionUpdateEvent,
    RiskDecisionEvent,
    SessionChangeEvent,
    SignalEvent,
    SystemHealthEvent,
    TickEvent,
    TradeCloseEvent,
    parse_event,
)


class TestEventBase:
    def test_to_stream_dict_flattens(self):
        e = Event(event_type="test", source="unit", data={"nested": {"value": 42}})
        flat = e.to_stream_dict()
        assert isinstance(flat, dict)
        assert all(isinstance(v, str) for v in flat.values())
        assert "data.nested.value" in flat

    def test_timestamp_auto(self):
        e = Event(event_type="test")
        assert e.timestamp is not None


class TestTickEvent:
    def test_defaults(self):
        e = TickEvent(symbol="EURUSD", bid=1.085, ask=1.0852, spread=0.0002)
        assert e.event_type == "tick"
        assert e.symbol == "EURUSD"


class TestOHLCVEvent:
    def test_defaults(self):
        e = OHLCVEvent(symbol="EURUSD", timeframe="M15", open=1.0, high=1.1, low=0.9, close=1.05)
        assert e.event_type == "ohlcv"


class TestSignalEvent:
    def test_defaults(self):
        e = SignalEvent(symbol="GBPUSD", direction="BUY", confidence=0.85)
        assert e.event_type == "signal"
        assert e.confidence == 0.85


class TestAgentEvents:
    def test_analysis_event(self):
        e = AgentAnalysisEvent(
            agent_id="A1", agent_name="Market Analyst",
            symbol="EURUSD", analysis="Strong uptrend", confidence=0.9,
        )
        assert e.event_type == "agent_analysis"

    def test_verdict_event(self):
        e = AgentVerdictEvent(
            agent_id="A7", agent_name="Arbitrator",
            symbol="EURUSD", verdict="BUY", conviction=0.82,
        )
        assert e.event_type == "agent_verdict"


class TestRiskDecisionEvent:
    def test_creation(self):
        e = RiskDecisionEvent(
            proposal_id="abc123", decision="APPROVE",
            original_size=0.1, approved_size=0.08,
        )
        assert e.event_type == "risk_decision"
        assert e.approved_size == 0.08


class TestOrderEvent:
    def test_market_order(self):
        e = OrderEvent(
            order_id="ord001", symbol="EURUSD",
            side="BUY", price=1.085, size=0.1,
        )
        assert e.order_type == "market"
        assert e.status == "pending"


class TestPositionUpdateEvent:
    def test_defaults(self):
        e = PositionUpdateEvent(position_id="pos001", symbol="EURUSD", pnl_pips=5.0)
        assert e.status == "open"


class TestTradeCloseEvent:
    def test_fields(self):
        e = TradeCloseEvent(
            trade_id="t001", symbol="EURUSD", side="BUY",
            entry_price=1.085, exit_price=1.087, pnl_pips=20.0,
            pnl_usd=200.0, rr_ratio=2.0, reason="TP1 hit",
        )
        assert e.reason == "TP1 hit"


class TestSessionChangeEvent:
    def test_fields(self):
        e = SessionChangeEvent(
            previous_session="TOKYO", new_session="LONDON",
            active_agents=["A1", "A2"], eligible_pairs=["EURUSD"],
        )
        assert len(e.active_agents) == 2


class TestNewsAlertEvent:
    def test_fields(self):
        e = NewsAlertEvent(
            headline="NFP beats expectations", impact="high",
            sentiment="bullish", affected_currencies=["USD"],
        )
        assert e.finbert_score == 0.0


class TestEventRegistry:
    def test_all_event_types_mapped(self):
        for et in EventType:
            assert et in EVENT_CLASS_MAP

    def test_all_streams_mapped(self):
        for et in EventType:
            assert et in STREAM_FOR_EVENT


class TestParseEvent:
    def test_parse_tick(self):
        raw = {"event_type": "tick", "symbol": "EURUSD", "bid": "1.085", "ask": "1.0852"}
        event = parse_event(raw)
        assert isinstance(event, TickEvent)

    def test_parse_unknown_type(self):
        raw = {"event_type": "unknown_type_xyz", "source": "test"}
        event = parse_event(raw)
        assert isinstance(event, Event)
