"""Tests for shared.models — Pydantic domain objects."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.constants import Verdict
from shared.models import (
    OHLCV,
    AgentMessage,
    CircuitBreaker,
    NewsItem,
    PerformanceMetrics,
    Position,
    RiskState,
    SessionState,
    Tick,
    Trade,
    TradeProposal,
    WSMessage,
)


class TestTick:
    def test_spread_auto_calculated(self):
        t = Tick(symbol="EURUSD", bid=1.08500, ask=1.08520)
        assert t.spread == pytest.approx(0.00020, abs=1e-6)

    def test_spread_preserves_explicit_value(self):
        t = Tick(symbol="EURUSD", bid=1.08500, ask=1.08520, spread=0.001)
        assert t.spread == 0.001

    def test_timestamp_auto_set(self):
        t = Tick(symbol="EURUSD", bid=1.0, ask=1.0)
        assert t.timestamp is not None


class TestOHLCV:
    def test_valid_candle(self, sample_ohlcv: OHLCV):
        assert sample_ohlcv.high >= sample_ohlcv.low
        assert sample_ohlcv.symbol == "EURUSD"

    def test_high_less_than_low_raises(self):
        with pytest.raises(ValidationError, match="high must be >= low"):
            OHLCV(
                symbol="EURUSD", timeframe="M15",
                open=1.0, high=0.9, low=1.1, close=1.0,
            )


class TestTradeProposal:
    def test_valid_buy(self, sample_proposal: TradeProposal):
        assert sample_proposal.direction == Verdict.BUY
        assert sample_proposal.sl < sample_proposal.entry

    def test_buy_sl_above_entry_raises(self):
        with pytest.raises(ValidationError, match="BUY stop-loss must be below entry"):
            TradeProposal(
                symbol="EURUSD", direction=Verdict.BUY,
                entry=1.08500, sl=1.08600, tp1=1.08700, conviction=0.8,
            )

    def test_sell_sl_below_entry_raises(self):
        with pytest.raises(ValidationError, match="SELL stop-loss must be above entry"):
            TradeProposal(
                symbol="EURUSD", direction=Verdict.SELL,
                entry=1.08500, sl=1.08400, tp1=1.08300, conviction=0.8,
            )

    def test_conviction_bounds(self):
        with pytest.raises(ValidationError):
            TradeProposal(
                symbol="EURUSD", direction=Verdict.BUY,
                entry=1.08500, sl=1.08350, tp1=1.08700, conviction=1.5,
            )

    def test_auto_id_generated(self, sample_proposal: TradeProposal):
        assert len(sample_proposal.id) == 12


class TestPosition:
    def test_defaults(self, sample_position: Position):
        assert sample_position.status == "open"
        assert sample_position.size == 0.1

    def test_auto_id(self):
        p = Position(symbol="GBPUSD", side=Verdict.SELL, entry_price=1.27)
        assert len(p.id) == 12


class TestTrade:
    def test_full_trade(self, sample_trade: Trade):
        assert sample_trade.pnl_pips == 20.0
        assert sample_trade.rr_ratio == 2.0


class TestAgentMessage:
    def test_defaults(self):
        m = AgentMessage(agent_id="A1", content="Test analysis")
        assert m.message_type == "analysis"
        assert m.agent_color == "#FFFFFF"


class TestRiskState:
    def test_defaults(self):
        rs = RiskState()
        assert rs.daily_loss_pct == 0.0
        assert rs.tilt_status == "normal"
        assert rs.circuit_breakers == []


class TestCircuitBreaker:
    def test_not_triggered(self):
        cb = CircuitBreaker(name="daily_loss")
        assert cb.triggered is False

    def test_triggered(self):
        cb = CircuitBreaker(name="daily_loss", triggered=True, reason="5% exceeded")
        assert cb.triggered is True


class TestSessionState:
    def test_creation(self):
        ss = SessionState(name="LONDON", max_trades=8, eligible_pairs=["EURUSD", "GBPUSD"])
        assert ss.size_multiplier == 1.0
        assert len(ss.eligible_pairs) == 2


class TestPerformanceMetrics:
    def test_defaults(self):
        pm = PerformanceMetrics()
        assert pm.total_trades == 0
        assert pm.sharpe_ratio == 0.0


class TestNewsItem:
    def test_creation(self, sample_news_item: NewsItem):
        assert sample_news_item.impact == "high"
        assert "USD" in sample_news_item.affected_currencies


class TestWSMessage:
    def test_creation(self):
        msg = WSMessage(channel="ticks", data={"symbol": "EURUSD"})
        assert msg.type == "update"
