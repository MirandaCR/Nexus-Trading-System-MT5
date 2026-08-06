"""
Nexus Agents — AgentState definition.

TypedDict for LangGraph + Pydantic model for runtime validation at boundaries.
"""
from __future__ import annotations

from typing import Any, Literal, Optional, TypedDict

from pydantic import BaseModel, Field


class AgentState(TypedDict):
    symbol: str
    timeframe: str
    session: str
    ml_signal: dict
    features: dict
    htf_context: dict
    regime_analysis: dict
    technical_analysis: str
    news_assessment: str
    intermarket_score: float
    flow_score: float
    bull_case: str
    bull_conviction: int
    bear_case: str
    bear_conviction: int
    verdict: Literal["BUY", "SELL", "NO_TRADE", "WATCH"]
    verdict_conviction: int
    verdict_reasoning: str
    risk_decision: Literal["APPROVE", "REJECT", "MODIFY"]
    position_size: float
    risk_reasoning: str
    open_positions: list
    order: Optional[dict]
    messages: list
    cycle_id: str
    errors: list
    trade_memory: dict


class AgentStateModel(BaseModel):
    """Pydantic mirror — validate at graph entry/exit."""

    symbol: str = "XAUUSD"
    timeframe: str = "M5"
    session: str = "OFF_HOURS"
    ml_signal: dict[str, Any] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)
    htf_context: dict[str, Any] = Field(default_factory=dict)
    regime_analysis: dict[str, Any] = Field(default_factory=dict)
    technical_analysis: str = ""
    news_assessment: str = ""
    intermarket_score: float = 0.0
    flow_score: float = 0.0
    bull_case: str = ""
    bull_conviction: int = 0
    bear_case: str = ""
    bear_conviction: int = 0
    verdict: Literal["BUY", "SELL", "NO_TRADE", "WATCH"] = "NO_TRADE"
    verdict_conviction: int = 0
    verdict_reasoning: str = ""
    risk_decision: Literal["APPROVE", "REJECT", "MODIFY"] = "REJECT"
    position_size: float = 0.0
    risk_reasoning: str = ""
    open_positions: list[Any] = Field(default_factory=list)
    order: Optional[dict[str, Any]] = None
    messages: list[Any] = Field(default_factory=list)
    cycle_id: str = ""
    errors: list[str] = Field(default_factory=list)
    trade_memory: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


def validate_state(state: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize agent state dict."""
    return AgentStateModel.model_validate(state).model_dump()


def initial_state(**kwargs: Any) -> AgentState:
    base = AgentStateModel(**kwargs).model_dump()
    return base  # type: ignore[return-value]
