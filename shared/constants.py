"""
Nexus Trader — system-wide constants.

Single source of truth for service ports, enums, default risk parameters,
session definitions, Redis stream names, and WebSocket channels.
"""
from __future__ import annotations

from enum import StrEnum, unique


# ── Service Discovery ────────────────────────────────────────────────────────

SERVICE_PORTS: dict[str, int] = {
    "nexus-api":      9000,
    "nexus-data":     9001,
    "nexus-features": 9002,
    "nexus-ml":       9003,
    "nexus-agents":   9004,
    "nexus-news":     9005,
    "nexus-risk":     9006,
    "nexus-executor": 9007,
    "nexus-monitor":  9008,
    "dashboard":      3100,
    "redis":          6379,
    "mlflow":         5000,
}


@unique
class ServiceName(StrEnum):
    API      = "nexus-api"
    DATA     = "nexus-data"
    FEATURES = "nexus-features"
    ML       = "nexus-ml"
    AGENTS   = "nexus-agents"
    NEWS     = "nexus-news"
    RISK     = "nexus-risk"
    EXECUTOR = "nexus-executor"
    MONITOR  = "nexus-monitor"


# ── Trading Modes ────────────────────────────────────────────────────────────

@unique
class TradingMode(StrEnum):
    PAPER    = "paper"
    LIVE     = "live"
    BACKTEST = "backtest"


# ── Agent Registry ───────────────────────────────────────────────────────────

@unique
class AgentID(StrEnum):
    A0  = "A0"
    A1  = "A1"
    A2  = "A2"
    A3  = "A3"
    A4  = "A4"
    A5  = "A5"
    A6  = "A6"
    A7  = "A7"
    A8  = "A8"
    A9  = "A9"
    A10 = "A10"
    A11 = "A11"
    A12 = "A12"


AGENT_NAMES: dict[str, str] = {
    AgentID.A0:  "Regime Analyst",
    AgentID.A1:  "Market Analyst",
    AgentID.A2:  "News Sentinel",
    AgentID.A3:  "Intermarket Analyst",
    AgentID.A4:  "Order Flow Specialist",
    AgentID.A5:  "Bull Researcher",
    AgentID.A6:  "Bear Researcher",
    AgentID.A7:  "Arbitrator",
    AgentID.A8:  "Risk Manager",
    AgentID.A9:  "Trade Executor",
    AgentID.A10: "Position Manager",
    AgentID.A11: "Session Governor",
    AgentID.A12: "Portfolio Strategist",
}

AGENT_COLORS: dict[str, str] = {
    AgentID.A0:  "#ff6600",   # neon-orange (regime)
    AgentID.A1:  "#00f5ff",   # neon-cyan
    AgentID.A2:  "#ffd700",   # neon-gold
    AgentID.A3:  "#bf40ff",   # neon-violet
    AgentID.A4:  "#39ff14",   # neon-green
    AgentID.A5:  "#39ff14",   # neon-green
    AgentID.A6:  "#ff1744",   # neon-red
    AgentID.A7:  "#ffd700",   # neon-gold
    AgentID.A8:  "#ff1744",   # neon-red
    AgentID.A9:  "#00f5ff",   # neon-cyan
    AgentID.A10: "#bf5af2",   # neon-purple
    AgentID.A11: "#39ff14",   # neon-green
    AgentID.A12: "#ffd700",   # neon-gold
}


# ── Trading Sessions ─────────────────────────────────────────────────────────

@unique
class SessionName(StrEnum):
    SYDNEY    = "SYDNEY"
    TOKYO     = "TOKYO"
    LONDON    = "LONDON"
    NEW_YORK  = "NEW_YORK"
    OVERLAP   = "OVERLAP"
    OFF_HOURS = "OFF_HOURS"


# Deprecated — prefer shared.sessions.detect_session(); kept for legacy imports.
SESSION_HOURS_UTC: dict[str, tuple[int, int]] = {
    SessionName.SYDNEY:    (22, 6),
    SessionName.TOKYO:     (0, 8),
    SessionName.LONDON:    (7, 16),
    SessionName.NEW_YORK:  (12, 21),
    SessionName.OVERLAP:   (12, 16),
    SessionName.OFF_HOURS: (21, 0),
}


# ── Verdict / Risk Decision ─────────────────────────────────────────────────

@unique
class Verdict(StrEnum):
    BUY      = "BUY"
    SELL     = "SELL"
    NO_TRADE = "NO_TRADE"
    WATCH    = "WATCH"


@unique
class RiskDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT  = "REJECT"
    MODIFY  = "MODIFY"


# ── WebSocket Channels ──────────────────────────────────────────────────────

@unique
class WSChannel(StrEnum):
    TICKS          = "ticks"
    OHLCV          = "ohlcv"
    SIGNALS        = "signals"
    AGENT_MESSAGES = "agent_messages"
    POSITIONS      = "positions"
    TRADES         = "trades"
    RISK_STATE     = "risk_state"
    SESSION_STATE  = "session_state"
    SYSTEM_HEALTH  = "system_health"
    NEWS           = "news"
    CALENDAR       = "calendar"
    PERFORMANCE    = "performance"
    ORDERS         = "orders"


# ── Redis Streams ────────────────────────────────────────────────────────────

@unique
class RedisStream(StrEnum):
    TICK_STREAM          = "stream:ticks"
    OHLCV_STREAM         = "stream:ohlcv"
    FEATURE_STREAM       = "stream:features"
    SIGNAL_STREAM        = "stream:signals"
    SIGNAL_AUTONOMOUS    = "stream:signals:autonomous"
    AGENT_ANALYSIS       = "stream:agent:analysis"
    AGENT_VERDICT        = "stream:agent:verdict"
    RISK_DECISION        = "stream:risk:decision"
    ORDER_STREAM         = "stream:orders"
    POSITION_UPDATE      = "stream:positions"
    TRADE_CLOSE          = "stream:trades:close"
    SESSION_CHANGE       = "stream:session:change"
    SYSTEM_HEALTH_STREAM = "stream:system:health"
    NEWS_ALERT           = "stream:news:alert"
    CALENDAR_STREAM      = "stream:news:calendar"


# ── Redis Cache Keys ─────────────────────────────────────────────────────────

REDIS_KEY_PREFIX      = "nexus:"
CACHE_OHLCV_PREFIX    = f"{REDIS_KEY_PREFIX}ohlcv:"
CACHE_FEATURE_PREFIX  = f"{REDIS_KEY_PREFIX}features:"
CACHE_POSITION_PREFIX = f"{REDIS_KEY_PREFIX}positions:"
CACHE_RISK_STATE      = f"{REDIS_KEY_PREFIX}risk:state"
CACHE_SESSION_STATE   = f"{REDIS_KEY_PREFIX}session:state"
CACHE_HEALTH_PREFIX   = f"{REDIS_KEY_PREFIX}health:"


# ── Default Risk Parameters ──────────────────────────────────────────────────

DEFAULT_RISK_PARAMS: dict[str, float | int] = {
    "max_risk_per_trade_pct":   2.0,
    "max_daily_loss_pct":       5.0,
    "max_weekly_drawdown_pct":  8.0,
    "max_open_positions":       3,
    "max_correlation_exposure":  0.70,
    "min_rr_ratio":             1.5,
    "max_spread_pips":          3.0,
    "min_conviction":           0.60,
    "position_size_cap_lots":   1.0,
    "trailing_stop_pips":       15,
    "break_even_trigger_pips":  20,
    "daily_trade_limit":        10,
    "cooldown_after_loss_sec":  300,
}


# ── Miscellaneous ────────────────────────────────────────────────────────────

HEARTBEAT_INTERVAL_SEC = 5
STREAM_MAX_LEN         = 10_000
DEFAULT_OHLCV_LIMIT    = 500
SUPPORTED_TIMEFRAMES   = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
