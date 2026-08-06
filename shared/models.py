"""
Nexus Trader — Pydantic v2 domain models.

Every data structure that crosses a service boundary is defined here so
serialisation / validation is consistent system-wide.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, model_validator

from .constants import (
    AgentID,
    RiskDecision,
    SessionName,
    TradingMode,
    Verdict,
    WSChannel,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# ── Market Data ──────────────────────────────────────────────────────────────

class Tick(BaseModel):
    symbol: str
    bid: float
    ask: float
    spread: float = 0.0
    timestamp: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="before")
    @classmethod
    def compute_spread(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("spread"):
            bid = data.get("bid", 0.0)
            ask = data.get("ask", 0.0)
            if bid and ask:
                data["spread"] = round(ask - bid, 6)
        return data


class OHLCV(BaseModel):
    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    timestamp: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def validate_hloc(self) -> "OHLCV":
        if self.high < self.low:
            raise ValueError("high must be >= low")
        return self


# ── Signals & Proposals ──────────────────────────────────────────────────────

class Signal(BaseModel):
    symbol: str
    direction: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    features: dict[str, float] = Field(default_factory=dict)
    ml_scores: dict[str, float] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)


class TradeProposal(BaseModel):
    id: str = Field(default_factory=_new_id)
    symbol: str
    direction: Verdict
    entry: float
    sl: float
    tp1: float
    tp2: float | None = None
    tp3: float | None = None
    conviction: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    timestamp: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def validate_sl_direction(self) -> "TradeProposal":
        if self.direction == Verdict.BUY and self.sl >= self.entry:
            raise ValueError("BUY stop-loss must be below entry")
        if self.direction == Verdict.SELL and self.sl <= self.entry:
            raise ValueError("SELL stop-loss must be above entry")
        return self


# ── Positions & Trades ───────────────────────────────────────────────────────

class Position(BaseModel):
    id: str = Field(default_factory=_new_id)
    symbol: str
    side: Verdict
    entry_price: float
    current_price: float = 0.0
    sl: float = 0.0
    tp1: float = 0.0
    tp2: float | None = None
    tp3: float | None = None
    size: float = 0.01
    pnl_pips: float = 0.0
    pnl_usd: float = 0.0
    duration: float = 0.0
    status: str = "open"
    opened_at: datetime = Field(default_factory=_utcnow)


class Trade(BaseModel):
    id: str = Field(default_factory=_new_id)
    symbol: str
    side: Verdict
    entry_price: float
    exit_price: float
    pnl_pips: float = 0.0
    pnl_usd: float = 0.0
    rr_ratio: float = 0.0
    duration: float = 0.0
    session: SessionName | str = ""
    conviction: float = 0.0
    opened_at: datetime = Field(default_factory=_utcnow)
    closed_at: datetime = Field(default_factory=_utcnow)


# ── Agent Comms ──────────────────────────────────────────────────────────────

class AgentMessage(BaseModel):
    agent_id: AgentID | str
    agent_name: str = ""
    agent_color: str = "#FFFFFF"
    message_type: str = "analysis"
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)


# ── Risk ─────────────────────────────────────────────────────────────────────

class CircuitBreaker(BaseModel):
    name: str
    triggered: bool = False
    triggered_at: datetime | None = None
    reason: str = ""


class RiskState(BaseModel):
    balance: float = 0.0
    equity: float = 0.0
    daily_loss_pct: float = 0.0
    weekly_dd_pct: float = 0.0
    open_positions: int = 0
    correlation_exposure: float = 0.0
    tilt_status: str = "normal"
    circuit_breakers: list[CircuitBreaker] = Field(default_factory=list)
    # Live enforced limits — the dashboard RiskMonitor reads these so the gauges
    # reflect the real runtime config instead of hardcoded frontend fallbacks.
    # Field names match dashboard/src/hooks/useRisk.ts mapBackendRisk().
    max_daily_loss_pct: float = 20.0
    max_weekly_drawdown_pct: float = 25.0
    open_positions_count: int = 0
    max_positions: int = 5
    max_correlation: float = 0.95
    consecutive_losses: int = 0
    current_session: str = ""


# ── Session ──────────────────────────────────────────────────────────────────

class SessionState(BaseModel):
    name: SessionName | str
    mode: TradingMode = TradingMode.PAPER
    active_agents: list[str] = Field(default_factory=list)
    max_trades: int = 10
    eligible_pairs: list[str] = Field(default_factory=list)
    volatility_mode: str = "normal"
    size_multiplier: float = 1.0


# ── System Health ────────────────────────────────────────────────────────────

class SystemHealth(BaseModel):
    service_name: str
    status: str = "healthy"
    latency_ms: float = 0.0
    cpu_pct: float = 0.0
    ram_pct: float = 0.0
    last_heartbeat: datetime = Field(default_factory=_utcnow)


# ── WebSocket ────────────────────────────────────────────────────────────────

class WSMessage(BaseModel):
    type: str = "update"
    channel: WSChannel | str
    timestamp: datetime = Field(default_factory=_utcnow)
    data: dict[str, Any] = Field(default_factory=dict)


# ── Performance ──────────────────────────────────────────────────────────────

class PerformanceMetrics(BaseModel):
    total_pnl: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    total_trades: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0


# ── News ─────────────────────────────────────────────────────────────────────

class NewsItem(BaseModel):
    headline: str
    source: str = ""
    timestamp: datetime = Field(default_factory=_utcnow)
    impact: str = "low"
    sentiment: str = "neutral"
    affected_currencies: list[str] = Field(default_factory=list)
    finbert_score: float = 0.0


class CalendarEvent(BaseModel):
    event_name: str
    currency: str
    impact: str = "low"
    forecast: str | None = None
    previous: str | None = None
    actual: str | None = None
    timestamp: datetime = Field(default_factory=_utcnow)
    source: str = "forexfactory"
