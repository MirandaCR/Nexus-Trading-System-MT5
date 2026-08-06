"""Tests for shared.constants — enums, registry maps, risk defaults."""
from __future__ import annotations

from shared.constants import (
    AGENT_COLORS,
    AGENT_NAMES,
    DEFAULT_RISK_PARAMS,
    HEARTBEAT_INTERVAL_SEC,
    SERVICE_PORTS,
    SESSION_HOURS_UTC,
    SUPPORTED_TIMEFRAMES,
    AgentID,
    RedisStream,
    RiskDecision,
    ServiceName,
    SessionName,
    TradingMode,
    Verdict,
    WSChannel,
)


class TestAgentID:
    def test_all_13_agents(self):
        assert len(AgentID) == 13

    def test_a0_through_a12(self):
        values = [a.value for a in AgentID]
        assert "A0" in values
        for i in range(1, 13):
            assert f"A{i}" in values

    def test_agent_names_cover_all_ids(self):
        for aid in AgentID:
            assert aid in AGENT_NAMES

    def test_agent_colors_cover_all_ids(self):
        for aid in AgentID:
            assert aid in AGENT_COLORS
            assert AGENT_COLORS[aid].startswith("#")


class TestSessionName:
    def test_session_count(self):
        assert len(SessionName) == 6

    def test_hours_defined(self):
        for name in SessionName:
            assert name in SESSION_HOURS_UTC
            start, end = SESSION_HOURS_UTC[name]
            assert 0 <= start <= 23
            assert 0 <= end <= 23


class TestVerdict:
    def test_values(self):
        assert Verdict.BUY == "BUY"
        assert Verdict.SELL == "SELL"
        assert Verdict.NO_TRADE == "NO_TRADE"
        assert Verdict.WATCH == "WATCH"


class TestRiskDecision:
    def test_values(self):
        assert RiskDecision.APPROVE == "APPROVE"
        assert RiskDecision.REJECT == "REJECT"
        assert RiskDecision.MODIFY == "MODIFY"


class TestServicePorts:
    def test_all_services_have_ports(self):
        for svc in ServiceName:
            assert svc.value in SERVICE_PORTS
            assert isinstance(SERVICE_PORTS[svc.value], int)

    def test_no_duplicate_ports(self):
        ports = list(SERVICE_PORTS.values())
        assert len(ports) == len(set(ports))


class TestDefaultRiskParams:
    def test_required_keys(self):
        required = [
            "max_risk_per_trade_pct",
            "max_daily_loss_pct",
            "max_weekly_drawdown_pct",
            "max_open_positions",
            "max_correlation_exposure",
            "min_rr_ratio",
        ]
        for key in required:
            assert key in DEFAULT_RISK_PARAMS

    def test_daily_loss_reasonable(self):
        assert 1.0 <= DEFAULT_RISK_PARAMS["max_daily_loss_pct"] <= 10.0

    def test_weekly_dd_gt_daily(self):
        assert DEFAULT_RISK_PARAMS["max_weekly_drawdown_pct"] > DEFAULT_RISK_PARAMS["max_daily_loss_pct"]


class TestRedisStream:
    def test_stream_prefix(self):
        for stream in RedisStream:
            assert stream.value.startswith("stream:")


class TestWSChannel:
    def test_channels_exist(self):
        assert len(WSChannel) > 0
        for ch in WSChannel:
            assert isinstance(ch.value, str)


class TestSupportedTimeframes:
    def test_includes_scalping_tf(self):
        assert "M1" in SUPPORTED_TIMEFRAMES
        assert "M5" in SUPPORTED_TIMEFRAMES
        assert "M15" in SUPPORTED_TIMEFRAMES

    def test_includes_htf(self):
        assert "H1" in SUPPORTED_TIMEFRAMES
        assert "H4" in SUPPORTED_TIMEFRAMES


class TestMisc:
    def test_heartbeat_interval(self):
        assert HEARTBEAT_INTERVAL_SEC > 0

    def test_trading_modes(self):
        assert TradingMode.PAPER == "paper"
        assert TradingMode.LIVE == "live"
        assert TradingMode.BACKTEST == "backtest"
