"""
Nexus Trader — shared package.

Re-exports every public symbol so consumers can do::

    from shared import Tick, OHLCV, RedisClient, setup_logger, ...
    from shared.constants import ServiceName, AgentID, ...
"""
from __future__ import annotations

# ── Constants ────────────────────────────────────────────────────────────────
from .constants import (
    AGENT_COLORS,
    AGENT_NAMES,
    CACHE_FEATURE_PREFIX,
    CACHE_HEALTH_PREFIX,
    CACHE_OHLCV_PREFIX,
    CACHE_POSITION_PREFIX,
    CACHE_RISK_STATE,
    CACHE_SESSION_STATE,
    DEFAULT_OHLCV_LIMIT,
    DEFAULT_RISK_PARAMS,
    HEARTBEAT_INTERVAL_SEC,
    REDIS_KEY_PREFIX,
    SERVICE_PORTS,
    STREAM_MAX_LEN,
    SUPPORTED_TIMEFRAMES,
    AgentID,
    RedisStream,
    RiskDecision,
    ServiceName,
    SessionName,
    SESSION_HOURS_UTC,
    TradingMode,
    Verdict,
    WSChannel,
)

# ── Pydantic Models ─────────────────────────────────────────────────────────
from .models import (
    AgentMessage,
    CalendarEvent,
    CircuitBreaker,
    NewsItem,
    OHLCV,
    PerformanceMetrics,
    Position,
    RiskState,
    SessionState,
    Signal,
    SystemHealth,
    Tick,
    Trade,
    TradeProposal,
    WSMessage,
)

# ── Event Schemas ────────────────────────────────────────────────────────────
from .events import (
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

# ── Clients ──────────────────────────────────────────────────────────────────
from .redis_client import RedisClient
from .duckdb_client import DuckDBClient
# fuelix_client removed — all LLM calls go through DeepSeek now

# ── Config ───────────────────────────────────────────────────────────────────
from .config_loader import ConfigLoader

# ── Logging ──────────────────────────────────────────────────────────────────
from .logger import (
    get_correlation_id,
    get_logger,
    new_correlation_id,
    set_correlation_id,
    setup_logger,
)

__all__ = [
    # constants
    "AGENT_COLORS",
    "AGENT_NAMES",
    "AgentID",
    "CACHE_FEATURE_PREFIX",
    "CACHE_HEALTH_PREFIX",
    "CACHE_OHLCV_PREFIX",
    "CACHE_POSITION_PREFIX",
    "CACHE_RISK_STATE",
    "CACHE_SESSION_STATE",
    "DEFAULT_OHLCV_LIMIT",
    "DEFAULT_RISK_PARAMS",
    "HEARTBEAT_INTERVAL_SEC",
    "REDIS_KEY_PREFIX",
    "RedisStream",
    "RiskDecision",
    "SERVICE_PORTS",
    "SESSION_HOURS_UTC",
    "STREAM_MAX_LEN",
    "SUPPORTED_TIMEFRAMES",
    "ServiceName",
    "SessionName",
    "TradingMode",
    "Verdict",
    "WSChannel",
    # models
    "AgentMessage",
    "CalendarEvent",
    "CircuitBreaker",
    "NewsItem",
    "OHLCV",
    "PerformanceMetrics",
    "Position",
    "RiskState",
    "SessionState",
    "Signal",
    "SystemHealth",
    "Tick",
    "Trade",
    "TradeProposal",
    "WSMessage",
    # events
    "AgentAnalysisEvent",
    "AgentVerdictEvent",
    "EVENT_CLASS_MAP",
    "Event",
    "EventType",
    "FeatureEvent",
    "NewsAlertEvent",
    "OHLCVEvent",
    "OrderEvent",
    "PositionUpdateEvent",
    "RiskDecisionEvent",
    "SessionChangeEvent",
    "SignalEvent",
    "STREAM_FOR_EVENT",
    "SystemHealthEvent",
    "TickEvent",
    "TradeCloseEvent",
    "parse_event",
    # clients
    "DuckDBClient",
    "RedisClient",
    # config
    "ConfigLoader",
    # logging
    "get_correlation_id",
    "get_logger",
    "new_correlation_id",
    "set_correlation_id",
    "setup_logger",
]
