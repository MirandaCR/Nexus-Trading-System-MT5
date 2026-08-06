"""
Nexus Agents — LangGraph state machine orchestration.

Builds the full agent pipeline as a compiled LangGraph StateGraph:

    session_check ──► regime_analysis (A0)
                      ──► parallel_analysis (A1,A2,A3,A4)
                      ──► parallel_debate (A5,A6)
                      ──► arbitrator (A7)
                      ──► risk_check (A8)
                      ──► execute (A9)
                      ──► position_manage (A10)
                      ──► portfolio_review (A12)

Conditional edges abort the graph early when:
  - Session is inactive   → STOP
  - Conviction < 70       → STOP
  - Risk decision = REJECT → STOP
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import structlog
from langgraph.graph import END, StateGraph

from shared.redis_client import RedisClient

from .agents.arbitrator import ArbitratorAgent
from .agents.bear_researcher import BearResearcherAgent
from .agents.bull_researcher import BullResearcherAgent
from .agents.intermarket import IntermarketAgent
from .agents.market_analyst import MarketAnalystAgent
from .agents.news_sentinel import NewsSentinelAgent
from .agents.order_flow import OrderFlowAgent
from .agents.portfolio_strategist import PortfolioStrategistAgent
from .agents.position_manager import PositionManagerAgent
from .agents.regime_analyst import RegimeAnalystAgent
from .agents.risk_manager import RiskManagerAgent
from .agents.session_governor import SessionGovernorAgent
from .agents.trade_executor import TradeExecutorAgent
from .state import AgentState

logger = structlog.get_logger(__name__)
_MIN_CONVICTION = int(os.getenv("NEXUS_MIN_VERDICT_CONVICTION", "70"))
_ALLOW_OFF_HOURS = os.getenv("NEXUS_ALLOW_OFF_HOURS", "false").lower() == "true"


def _log_transition(node_name: str) -> None:
    """Synchronous helper to mark state transitions (used inside node wrappers)."""
    structlog.get_logger(__name__).info(
        "graph.transition",
        node=node_name,
        ts=datetime.now(timezone.utc).isoformat(),
    )


def build_graph(redis_client: RedisClient) -> Any:
    """Construct, compile and return the runnable LangGraph agent pipeline."""

    # ── Instantiate all agents ────────────────────────────────────────────
    session_governor = SessionGovernorAgent(redis_client=redis_client)
    regime_analyst = RegimeAnalystAgent(redis_client=redis_client)
    market_analyst = MarketAnalystAgent(redis_client=redis_client)
    news_sentinel = NewsSentinelAgent(redis_client=redis_client)
    intermarket = IntermarketAgent(redis_client=redis_client)
    order_flow = OrderFlowAgent(redis_client=redis_client)
    bull_researcher = BullResearcherAgent(redis_client=redis_client)
    bear_researcher = BearResearcherAgent(redis_client=redis_client)
    arbitrator = ArbitratorAgent(redis_client=redis_client)
    risk_manager = RiskManagerAgent(redis_client=redis_client)
    trade_executor = TradeExecutorAgent(redis_client=redis_client)
    position_manager = PositionManagerAgent(redis_client=redis_client)
    portfolio_strategist = PortfolioStrategistAgent(redis_client=redis_client)

    # ── Node wrappers ─────────────────────────────────────────────────────

    async def session_check_node(state: AgentState) -> AgentState:
        _log_transition("session_check")
        return await session_governor.invoke(state)

    async def regime_analysis_node(state: AgentState) -> AgentState:
        """Run A0 Regime Analyst — classifies TRENDING/RANGING/BREAKOUT."""
        _log_transition("regime_analysis")
        try:
            return await regime_analyst.invoke(state)
        except Exception as exc:
            state["errors"].append(f"A0_RegimeAnalyst: {exc}")
            state["regime_analysis"] = {
                "regime": "UNKNOWN",
                "regime_strength": 0,
                "suggested_approach": "TREND_FOLLOW",
            }
            return state

    async def parallel_analysis_node(state: AgentState) -> AgentState:
        """Run A1, A2, A3, A4 concurrently — failures are logged, not fatal."""
        _log_transition("parallel_analysis")

        async def _safe(agent, st: AgentState, label: str) -> AgentState:
            try:
                return await agent.invoke(st)
            except Exception as exc:
                st["errors"].append(f"{label}: {exc}")
                await logger.awarning("graph.agent_error", agent=label, error=str(exc))
                return st

        results = await asyncio.gather(
            _safe(market_analyst, {**state}, "A1_MarketAnalyst"),
            _safe(news_sentinel, {**state}, "A2_NewsSentinel"),
            _safe(intermarket, {**state}, "A3_Intermarket"),
            _safe(order_flow, {**state}, "A4_OrderFlow"),
        )

        merged: AgentState = {**state}
        for r in results:
            if r.get("technical_analysis"):
                merged["technical_analysis"] = r["technical_analysis"]
            if r.get("news_assessment"):
                merged["news_assessment"] = r["news_assessment"]
            if r.get("intermarket_score"):
                merged["intermarket_score"] = r["intermarket_score"]
            if r.get("flow_score"):
                merged["flow_score"] = r["flow_score"]
            # Preserve regime_analysis from A0 (set before parallel_analysis)
            if r.get("regime_analysis") and not merged.get("regime_analysis"):
                merged["regime_analysis"] = r["regime_analysis"]
            merged["messages"] = merged.get("messages", []) + r.get("messages", [])
            merged["errors"] = merged.get("errors", []) + [
                e for e in r.get("errors", []) if e not in merged.get("errors", [])
            ]
        return merged

    async def parallel_debate_node(state: AgentState) -> AgentState:
        """Run A5 and A6 concurrently."""
        _log_transition("parallel_debate")

        async def _safe(agent, st: AgentState, label: str) -> AgentState:
            try:
                return await agent.invoke(st)
            except Exception as exc:
                st["errors"].append(f"{label}: {exc}")
                await logger.awarning("graph.agent_error", agent=label, error=str(exc))
                return st

        bull_result, bear_result = await asyncio.gather(
            _safe(bull_researcher, {**state}, "A5_BullResearcher"),
            _safe(bear_researcher, {**state}, "A6_BearResearcher"),
        )

        merged: AgentState = {**state}
        merged["bull_case"] = bull_result.get("bull_case", "")
        merged["bull_conviction"] = bull_result.get("bull_conviction", 0)
        merged["bear_case"] = bear_result.get("bear_case", "")
        merged["bear_conviction"] = bear_result.get("bear_conviction", 0)
        merged["messages"] = (
            merged.get("messages", [])
            + bull_result.get("messages", [])
            + bear_result.get("messages", [])
        )
        merged["errors"] = list(
            {*merged.get("errors", []), *bull_result.get("errors", []), *bear_result.get("errors", [])}
        )
        return merged

    async def arbitrator_node(state: AgentState) -> AgentState:
        _log_transition("arbitrator")
        return await arbitrator.invoke(state)

    async def risk_check_node(state: AgentState) -> AgentState:
        _log_transition("risk_check")
        return await risk_manager.invoke(state)

    async def execute_node(state: AgentState) -> AgentState:
        _log_transition("execute")
        return await trade_executor.invoke(state)

    async def position_manage_node(state: AgentState) -> AgentState:
        _log_transition("position_manage")
        return await position_manager.invoke(state)

    async def portfolio_review_node(state: AgentState) -> AgentState:
        _log_transition("portfolio_review")
        return await portfolio_strategist.invoke(state)

    # ── Conditional edge functions ────────────────────────────────────────

    async def _get_session_trade_count() -> int:
        """Read how many trades have been executed in the current session."""
        try:
            val = await redis_client.cache_get("session:trade_count")
            return int(val) if val else 0
        except Exception:
            return 0

    def should_continue_after_session(state: AgentState) -> str:
        session = state.get("session", "").upper()
        if session in ("UNKNOWN", ""):
            return "stop"
        if not _ALLOW_OFF_HOURS and session == "OFF_HOURS":
            return "stop"

        # Enforce max_trades per session from session config
        for msg in state.get("messages", []):
            if isinstance(msg, dict) and msg.get("max_trades") is not None:
                max_trades = int(msg["max_trades"])
                # Read trade count synchronously from state (set by session governor)
                current_count = state.get("_session_trade_count", 0)
                if current_count >= max_trades > 0:
                    logger.warning(
                        "graph.max_trades_reached",
                        session=session,
                        count=current_count,
                        max=max_trades,
                    )
                    return "stop"
                break
        return "continue"

    def should_continue_after_verdict(state: AgentState) -> str:
        conviction = state.get("verdict_conviction", 0)
        verdict = state.get("verdict", "NO_TRADE")
        if verdict in ("NO_TRADE", "WATCH") or conviction < _MIN_CONVICTION:
            return "stop"
        return "continue"

    def should_continue_after_risk(state: AgentState) -> str:
        if state.get("risk_decision") == "REJECT":
            return "stop"
        return "continue"

    # ── Build the graph ───────────────────────────────────────────────────

    graph = StateGraph(AgentState)

    graph.add_node("session_check", session_check_node)
    graph.add_node("regime_analysis", regime_analysis_node)
    graph.add_node("parallel_analysis", parallel_analysis_node)
    graph.add_node("parallel_debate", parallel_debate_node)
    graph.add_node("arbitrator", arbitrator_node)
    graph.add_node("risk_check", risk_check_node)
    graph.add_node("execute", execute_node)
    graph.add_node("position_manage", position_manage_node)
    graph.add_node("portfolio_review", portfolio_review_node)

    graph.set_entry_point("session_check")

    graph.add_conditional_edges(
        "session_check",
        should_continue_after_session,
        {"continue": "regime_analysis", "stop": END},
    )
    graph.add_edge("regime_analysis", "parallel_analysis")
    graph.add_edge("parallel_analysis", "parallel_debate")
    graph.add_edge("parallel_debate", "arbitrator")
    graph.add_conditional_edges(
        "arbitrator",
        should_continue_after_verdict,
        {"continue": "risk_check", "stop": END},
    )
    graph.add_conditional_edges(
        "risk_check",
        should_continue_after_risk,
        {"continue": "execute", "stop": END},
    )
    graph.add_edge("execute", "position_manage")
    graph.add_edge("position_manage", "portfolio_review")
    graph.add_edge("portfolio_review", END)

    compiled = graph.compile()
    logger.info("graph.compiled", nodes=list(graph.nodes.keys()))
    return compiled
