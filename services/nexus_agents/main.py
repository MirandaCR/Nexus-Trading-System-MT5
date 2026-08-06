"""
Nexus Agents — FastAPI application (Service 5, port 8004).

Exposes REST endpoints for agent status, evaluation triggers, and reasoning
retrieval.  A background task consumes Signal events from Redis Streams
and kicks off the LangGraph agent evaluation cycle.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from shared.constants import RedisStream, ServiceName
from shared.events import (
    AgentAnalysisEvent,
    AgentVerdictEvent,
    Event,
    SignalEvent,
)
from shared.redis_client import RedisClient

from .graph import build_graph
from .research import StrategyResearcher
from .state import AgentState, validate_state
from shared.orchestrator import claim_active_signal, should_defer
from .tools.market_tools import get_htf_context
from .tools.risk_tools import get_open_positions

from shared import setup_logger as _setup_logger
_setup_logger("nexus-agents")
logger = structlog.get_logger(__name__)

SERVICE_NAME = ServiceName.AGENTS
SERVICE_PORT = int(os.getenv("NEXUS_SERVICE_PORT", "8004"))
CONSUMER_GROUP = "nexus-agents-cg"
CONSUMER_NAME = f"agent-worker-{uuid.uuid4().hex[:6]}"

# Research scheduler config
RESEARCH_INTERVAL_SEC = int(os.getenv("NEXUS_RESEARCH_INTERVAL_SEC", str(60 * 30)))
RESEARCH_BATCH_SIZE = int(os.getenv("NEXUS_RESEARCH_BATCH", "2"))
RESEARCH_DISABLED = os.getenv("NEXUS_DISABLE_RESEARCH", "").lower() in ("1", "true", "yes")

redis_client = RedisClient()
_agent_graph = None
_consumer_task: asyncio.Task | None = None
_research_task: asyncio.Task | None = None
_heartbeat_task: asyncio.Task | None = None
_researcher: StrategyResearcher | None = None

_cycle_store: dict[str, dict[str, Any]] = {}
_agent_statuses: dict[str, dict[str, Any]] = {}
_cycle_active: bool = False


# ── Response Models ───────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    service: str
    status: str
    timestamp: datetime
    redis: bool


class AgentStatusResponse(BaseModel):
    agents: dict[str, Any]
    active_cycles: int
    total_cycles_completed: int


class EvaluationResponse(BaseModel):
    cycle_id: str
    symbol: str
    status: str
    verdict: str | None = None
    conviction: int | None = None
    reasoning: str | None = None


class ReasoningResponse(BaseModel):
    cycle_id: str
    state: dict[str, Any]
    messages: list[dict[str, Any]]
    errors: list[str]


# ── Signal Consumer ───────────────────────────────────────────────────────────

CYCLE_TIMEOUT_SEC = int(os.getenv("NEXUS_CYCLE_TIMEOUT_SEC", "300"))
AGENT_SIGNAL_COOLDOWN_SEC = int(os.getenv("NEXUS_AGENT_SIGNAL_COOLDOWN_SEC", "120"))
_last_agent_cycle_ts: dict[str, float] = {}


async def _consume_signals() -> None:
    """Background loop: read Signal events and trigger agent evaluation."""
    await logger.ainfo("consumer.starting", group=CONSUMER_GROUP)
    async for event in redis_client.subscribe_stream(
        RedisStream.SIGNAL_STREAM,
        CONSUMER_GROUP,
        CONSUMER_NAME,
    ):
        if event.event_type != "signal":
            continue
        try:
            signal_ev: SignalEvent = event  # type: ignore[assignment]

            # Throttle: one LangGraph cycle per symbol per cooldown window
            now = time.monotonic()
            sym_key = signal_ev.symbol.upper()
            last_ts = _last_agent_cycle_ts.get(sym_key, 0.0)
            if last_ts and (now - last_ts) < AGENT_SIGNAL_COOLDOWN_SEC:
                continue
            _last_agent_cycle_ts[sym_key] = now

            cycle_id = uuid.uuid4().hex[:12]
            await logger.ainfo(
                "consumer.signal_received",
                symbol=signal_ev.symbol,
                cycle_id=cycle_id,
            )
            await asyncio.wait_for(
                _run_evaluation_cycle(
                    symbol=signal_ev.symbol,
                    ml_signal={
                        "direction": str(signal_ev.direction),
                        "confidence": signal_ev.confidence,
                        "ml_scores": signal_ev.ml_scores,
                    },
                    features=signal_ev.features,
                    cycle_id=cycle_id,
                ),
                timeout=CYCLE_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            if cycle_id in _cycle_store:
                _cycle_store[cycle_id]["status"] = "error"
                _cycle_store[cycle_id]["error"] = f"Timeout after {CYCLE_TIMEOUT_SEC}s"
            await logger.awarning(
                "consumer.cycle_timeout",
                cycle_id=cycle_id,
                timeout_sec=CYCLE_TIMEOUT_SEC,
            )
        except Exception:
            await logger.aexception("consumer.evaluation_error")


async def _run_evaluation_cycle(
    symbol: str,
    ml_signal: dict | None = None,
    features: dict | None = None,
    cycle_id: str | None = None,
) -> AgentState:
    """Execute the full LangGraph agent pipeline for one symbol."""
    global _agent_graph

    cycle_id = cycle_id or uuid.uuid4().hex[:12]

    # ── Kill switch check ─────────────────────────────────────
    kill_switch = await redis_client.cache_get("system:kill_switch")
    if kill_switch:
        await logger.awarning(
            "cycle.kill_switch_active",
            symbol=symbol,
            cycle_id=cycle_id,
        )
        return {
            "symbol": symbol,
            "verdict": "NO_TRADE",
            "verdict_conviction": 0,
            "verdict_reasoning": "Kill switch is active — skipping evaluation.",
            "cycle_id": cycle_id,
            "errors": [],
            "messages": [],
        }

    session_state = await redis_client.cache_get("session:state")
    current_session = session_state.get("name", "UNKNOWN") if session_state else "UNKNOWN"

    try:
        symbol_positions = await get_open_positions(symbol)
    except Exception:
        symbol_positions = []

    # Fetch higher-timeframe context relative to operational timeframe.
    # Respect the current trading mode (M5 for scalping, M15 for swing).
    # Use env var as primary source — set by the trading mode switch API;
    # avoids cross-service import that breaks when services run in
    # separate processes.
    operational_tf = os.getenv("NEXUS_OPERATIONAL_TF", "M5")
    try:
        dyn_raw = await redis_client.r.get("nexus:dynamic:tf")
        if dyn_raw:
            operational_tf = json.loads(dyn_raw).get("operational_tf", operational_tf)
    except Exception:
        pass
    try:
        from shared import runtime_settings as _rs
        _cached = _rs.get_cached_settings()
        operational_tf = _cached.get("operational_tf", operational_tf)
    except Exception:
        pass
    try:
        htf_ctx = await get_htf_context(symbol, operational_tf)
    except Exception:
        htf_ctx = {"bias": "NEUTRAL", "alignment_score": 0, "htf_details": {},
                    "operational_tf": operational_tf, "htf_list": []}

    # Orchestrator: defer if ML autonomous has priority lock
    defer, defer_reason = await should_defer(redis_client.r, symbol, "genai_agents")
    if defer:
        await logger.ainfo("cycle.deferred", symbol=symbol, reason=defer_reason, cycle_id=cycle_id)
        return validate_state({
            "symbol": symbol,
            "verdict": "NO_TRADE",
            "verdict_conviction": 0,
            "verdict_reasoning": defer_reason,
            "cycle_id": cycle_id,
            "errors": [],
            "messages": [],
        })

    # Fetch trade memory snapshot for agents to reference
    trade_memory_snapshot: dict = {}
    try:
        raw_str = await redis_client.r.get("nexus:trade_memory:metrics")
        if raw_str:
            trade_memory_snapshot = json.loads(raw_str)
    except Exception:
        pass

    initial_state: AgentState = validate_state({
        "symbol": symbol,
        "timeframe": operational_tf,
        "session": current_session,
        "ml_signal": ml_signal or {},
        "features": features or {},
        "htf_context": htf_ctx,
        "regime_analysis": {},
        "technical_analysis": "",
        "news_assessment": "",
        "intermarket_score": 0.0,
        "flow_score": 0.0,
        "bull_case": "",
        "bull_conviction": 0,
        "bear_case": "",
        "bear_conviction": 0,
        "verdict": "NO_TRADE",
        "verdict_conviction": 0,
        "verdict_reasoning": "",
        "risk_decision": "REJECT",
        "position_size": 0.0,
        "risk_reasoning": "",
        "open_positions": symbol_positions,
        "order": None,
        "messages": [],
        "cycle_id": cycle_id,
        "errors": [],
        "trade_memory": trade_memory_snapshot,
    })

    global _cycle_active

    HEALTH_TTL = 180  # survive long LLM calls without expiring

    _cycle_store[cycle_id] = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "state": initial_state,
    }

    _cycle_active = True
    now_iso = datetime.now(timezone.utc).isoformat()
    for aid in ("A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11", "A12"):
        await redis_client.cache_set(
            f"health:{aid}",
            {"status": "processing", "ts": now_iso, "cycle_id": cycle_id},
            ttl_sec=CYCLE_TIMEOUT_SEC,
        )

    try:
        final_state = validate_state(await _agent_graph.ainvoke(initial_state))

        if final_state.get("verdict") in ("BUY", "SELL") and final_state.get("risk_decision") == "APPROVE":
            await claim_active_signal(
                redis_client.r,
                symbol,
                "genai_agents",
                {
                    "direction": final_state.get("verdict"),
                    "confidence": final_state.get("verdict_conviction", 0) / 100.0,
                },
            )

        _cycle_store[cycle_id]["status"] = "completed"
        _cycle_store[cycle_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        _cycle_store[cycle_id]["state"] = final_state

        await _publish_results(final_state, cycle_id)

        now_iso = datetime.now(timezone.utc).isoformat()
        for aid in ("A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11", "A12"):
            await redis_client.cache_set(
                f"health:{aid}",
                {"status": "online", "ts": now_iso, "cycle_id": cycle_id},
                ttl_sec=HEALTH_TTL,
            )

        await logger.ainfo(
            "cycle.completed",
            cycle_id=cycle_id,
            symbol=symbol,
            verdict=final_state.get("verdict"),
            conviction=final_state.get("verdict_conviction"),
        )
        return final_state

    except Exception as exc:
        _cycle_store[cycle_id]["status"] = "error"
        _cycle_store[cycle_id]["error"] = str(exc)
        now_iso = datetime.now(timezone.utc).isoformat()
        for aid in ("A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11", "A12"):
            await redis_client.cache_set(
                f"health:{aid}",
                {"status": "error", "ts": now_iso, "cycle_id": cycle_id},
                ttl_sec=HEALTH_TTL,
            )
        await logger.aexception("cycle.error", cycle_id=cycle_id)
        raise
    finally:
        _cycle_active = False


async def _publish_results(state: AgentState, cycle_id: str) -> None:
    """Publish agent analysis and verdict events to Redis Streams."""
    analysis_event = AgentAnalysisEvent(
        source=str(SERVICE_NAME),
        correlation_id=cycle_id,
        agent_id="A7",
        agent_name="Arbitrator",
        symbol=state["symbol"],
        analysis=state.get("verdict_reasoning", ""),
        confidence=state.get("verdict_conviction", 0) / 100.0,
        metadata={
            "technical_analysis": state.get("technical_analysis", "")[:200],
            "news_assessment": state.get("news_assessment", "")[:200],
            "intermarket_score": state.get("intermarket_score", 0.0),
            "flow_score": state.get("flow_score", 0.0),
        },
    )
    await redis_client.publish_event(RedisStream.AGENT_ANALYSIS, analysis_event)

    # Pull entry/SL/TP from the order computed by A9 (Trade Executor)
    # so the downstream risk service can evaluate real prices instead
    # of dropping the verdict due to entry==0 or sl==0.
    order = state.get("order") or {}
    verdict_data: dict[str, Any] = {}
    if order:
        verdict_data["entry"] = order.get("entry", 0)
        verdict_data["sl"] = order.get("sl", 0)
        verdict_data["tp1"] = order.get("tp1", 0)
        verdict_data["tp2"] = order.get("tp2", 0)
        verdict_data["tp3"] = order.get("tp3", 0)
        verdict_data["tp"] = order.get("tp1", 0)
        verdict_data["size"] = order.get("size", 0)
        verdict_data["order_type"] = order.get("order_type", "market")
        verdict_data["trailing_stop"] = order.get("trailing_stop", 0)
        verdict_data["bid"] = order.get("bid", 0)
        verdict_data["ask"] = order.get("ask", 0)
        verdict_data["comment"] = order.get("comment", "")
        verdict_data["strategy"] = order.get("strategy", "genai")
        # A9 already sent the order via REST — tell risk/executor not to duplicate
        if str(order.get("status", "")).lower() == "sent":
            verdict_data["already_executed"] = True
            verdict_data["order_id"] = (order.get("broker_response") or {}).get("order_id", "")

    verdict_event = AgentVerdictEvent(
        source=str(SERVICE_NAME),
        correlation_id=cycle_id,
        agent_id="A7",
        agent_name="Arbitrator",
        symbol=state["symbol"],
        verdict=state.get("verdict", "NO_TRADE"),
        conviction=state.get("verdict_conviction", 0) / 100.0,
        reasoning=state.get("verdict_reasoning", ""),
        data=verdict_data,
    )
    await redis_client.publish_event(RedisStream.AGENT_VERDICT, verdict_event)

    # Mark active signal so the autonomous ML pipeline defers for this symbol
    if state.get("verdict") in ("BUY", "SELL"):
        try:
            import json as _json
            await redis_client.r.set(
                f"nexus:active_signal:{state['symbol']}",
                _json.dumps({
                    "source": "genai_agents",
                    "direction": state.get("verdict"),
                    "conviction": state.get("verdict_conviction", 0),
                    "cycle_id": cycle_id,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }),
                ex=120,
            )
        except Exception:
            pass

    # Cache the latest decision so the dashboard can render a live panel
    # without having to subscribe to the Redis stream itself.
    if _researcher is not None:
        try:
            await _researcher.registry.publish_live_decision(
                state["symbol"],
                {
                    "cycle_id": cycle_id,
                    "verdict": state.get("verdict", "NO_TRADE"),
                    "conviction": state.get("verdict_conviction", 0),
                    "reasoning": state.get("verdict_reasoning", "")[:600],
                    "session": state.get("session", "UNKNOWN"),
                    "risk_decision": state.get("risk_decision", "REJECT"),
                    "risk_reasoning": state.get("risk_reasoning", "")[:400],
                    "position_size": state.get("position_size", 0.0),
                    "ml_signal": state.get("ml_signal", {}),
                    "regime_analysis": state.get("regime_analysis", {}),
                    "htf_bias": state.get("htf_context", {}).get("bias", "NEUTRAL"),
                    "htf_alignment": state.get("htf_context", {}).get("alignment_score", 0),
                    "entry": order.get("entry", 0),
                    "sl": order.get("sl", 0),
                    "tp1": order.get("tp1", 0),
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:
            await logger.awarning("decision.cache_failed", symbol=state["symbol"])


# ── Strategy research scheduler ──────────────────────────────────────────────

async def _research_loop() -> None:
    """Periodically propose, evaluate and persist new strategies."""
    if _researcher is None:
        return
    try:
        await _researcher.ensure_seed()
    except Exception:
        await logger.aexception("research.seed_failed")

    if RESEARCH_DISABLED:
        await logger.ainfo("research.disabled_via_env")
        return

    cycle_count = 0
    while True:
        cycle_count += 1
        # Re-evaluating the whole registry is CPU-heavy (Python-loop backtests).
        # Do it only every 3rd cycle so the agents service stays responsive
        # (heartbeats / health) the rest of the time.
        if cycle_count % 3 == 1:
            try:
                n_refreshed = await _researcher.re_evaluate_all()
                await logger.ainfo("research.refreshed_existing", count=n_refreshed)
            except Exception:
                await logger.aexception("research.refresh_failed")

        # Default cadence; shortened automatically when we still lack a
        # tradeable strategy for the current regime (reduce dead time).
        next_sleep = RESEARCH_INTERVAL_SEC
        try:
            from shared import runtime_settings
            settings = await runtime_settings.get_settings(_researcher.redis)
            base_interval = int(settings.get("research_interval_sec", RESEARCH_INTERVAL_SEC))

            # Emergency research trigger: if trade memory shows a losing streak ≥ 5,
            # run research immediately so the LLM can design for the current failure mode.
            try:
                _tm_raw = await redis_client.r.get("nexus:trade_memory:metrics")
                if _tm_raw:
                    _tm = json.loads(_tm_raw)
                    _streak = _tm.get("recent_streak", {})
                    if _streak.get("type") == "loss" and _streak.get("count", 0) >= 5:
                        await logger.awarning(
                            "research.emergency_trigger",
                            streak=_streak["count"],
                            reason="losing streak >= 5 — running immediate research cycle",
                        )
            except Exception:
                pass

            summary = await _researcher.run_adaptive_cycle()
            searched = bool(summary.get("searched"))
            has_active_fit = summary.get("had_fitting_active") or len(summary.get("accepted", [])) > 0
            await logger.ainfo(
                "research.cycle_done",
                cycle=cycle_count,
                regime=summary.get("regime"),
                accepted=len(summary.get("accepted", [])),
                rejected=len(summary.get("rejected", [])),
                proposed=summary.get("proposed", 0),
                rounds=summary.get("rounds_used", 1),
                searched=searched,
                improved=len(summary.get("improved", [])),
            )
            # If we still have nothing tradeable for this regime, hunt sooner —
            # but keep a healthy floor so research never hogs the CPU continuously
            # (which would freeze heartbeats and make agents render OFFLINE).
            next_sleep = base_interval if has_active_fit else max(600, base_interval // 3)
        except asyncio.CancelledError:
            raise
        except Exception:
            await logger.aexception("research.cycle_failed")

        try:
            await asyncio.sleep(next_sleep)
        except asyncio.CancelledError:
            raise


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent_graph, _consumer_task, _research_task, _researcher

    await redis_client.connect()
    _agent_graph = build_graph(redis_client)
    _researcher = StrategyResearcher(redis_client)

    _consumer_task = asyncio.create_task(_consume_signals())
    _research_task = asyncio.create_task(_research_loop())

    # Daily brief service (Fase 4)
    from .daily_brief import daily_brief_loop as _daily_brief_loop
    _symbols = os.getenv("STREAM_SYMBOLS", "XAUUSD").split(",")
    _daily_brief_task = asyncio.create_task(_daily_brief_loop(redis_client.r, _symbols))

    HEARTBEAT_TTL = 300
    HEARTBEAT_INTERVAL = 15
    _AGENT_IDS = ("A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11", "A12")

    async def _agent_heartbeat():
        while True:
            try:
                now_iso = datetime.now(timezone.utc).isoformat()
                for aid in _AGENT_IDS:
                    if _cycle_active:
                        existing = await redis_client.cache_get(f"health:{aid}")
                        if isinstance(existing, dict) and existing.get("status") == "processing":
                            continue
                    await redis_client.cache_set(
                        f"health:{aid}",
                        {
                            "status": "processing" if _cycle_active else "idle",
                            "ts": now_iso,
                            "service": "nexus-agents",
                        },
                        ttl_sec=HEARTBEAT_TTL,
                    )
            except Exception as exc:
                await logger.awarning("heartbeat.failed", error=str(exc))
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    _heartbeat_task = asyncio.create_task(_agent_heartbeat())

    # Shadow trade resolver (Fase 4) — every 5 min, resolve any hypothetical
    # trades whose 60-minute window has elapsed so gate tuning (conflict-lock,
    # ergodic circuit breaker, institutional thresholds) can be validated
    # against what rejected signals would actually have done.
    async def _shadow_trade_resolver_loop():
        from .shadow_trades import resolve_due_shadow_trades
        from .tools.market_tools import get_price as _get_price_dict

        async def _mid_price(symbol: str) -> float | None:
            try:
                tick = await _get_price_dict(symbol)
                bid, ask = tick.get("bid", 0), tick.get("ask", 0)
                if bid and ask:
                    return (bid + ask) / 2
                return bid or ask or None
            except Exception:
                return None

        await asyncio.sleep(90)
        while True:
            try:
                n = await resolve_due_shadow_trades(redis_client, _mid_price)
                if n:
                    await logger.ainfo("shadow_trades.resolved", count=n)
            except Exception:
                await logger.awarning("shadow_trades.resolve_loop_error", exc_info=True)
            await asyncio.sleep(300)

    _shadow_task = asyncio.create_task(_shadow_trade_resolver_loop())

    await logger.ainfo("service.started", service=str(SERVICE_NAME), port=SERVICE_PORT)

    yield

    for task in (_consumer_task, _research_task, _heartbeat_task, _daily_brief_task, _shadow_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    await redis_client.disconnect()
    await logger.ainfo("service.stopped", service=str(SERVICE_NAME))


# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Nexus Agents — Multi-Agent Layer",
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health():
    redis_ok = await redis_client.health_check()
    return HealthResponse(
        service=str(SERVICE_NAME),
        status="healthy" if redis_ok else "degraded",
        timestamp=datetime.now(timezone.utc),
        redis=redis_ok,
    )


@app.get("/api/agents/brain/budget")
async def get_brain_budget():
    """Daily LLM cost tracker — shows remaining budget and token usage."""
    from .brain_budget import budget_status
    try:
        return await budget_status(redis_client.r)
    except Exception as e:
        return {"error": str(e), "allowed": True, "cost_usd": 0.0}


@app.get("/api/agents/shadow-trades/stats")
async def get_shadow_trade_stats(limit: int = 500):
    """What rejected/downgraded signals would have done — validates whether
    the conflict-lock, ergodic circuit breaker and institutional thresholds
    are filtering noise or costing real edge."""
    from .shadow_trades import get_shadow_stats
    try:
        return await get_shadow_stats(redis_client, limit=limit)
    except Exception as e:
        return {"error": str(e), "total": 0}


@app.get("/api/agents/status", response_model=AgentStatusResponse)
async def get_agent_status():
    active = sum(1 for c in _cycle_store.values() if c["status"] == "running")
    completed = sum(1 for c in _cycle_store.values() if c["status"] == "completed")
    return AgentStatusResponse(
        agents=_agent_statuses,
        active_cycles=active,
        total_cycles_completed=completed,
    )


@app.post("/api/agents/evaluate/{symbol}", response_model=EvaluationResponse)
async def evaluate_symbol(symbol: str):
    cycle_id = uuid.uuid4().hex[:12]
    try:
        final_state = await _run_evaluation_cycle(
            symbol=symbol,
            cycle_id=cycle_id,
        )
        return EvaluationResponse(
            cycle_id=cycle_id,
            symbol=symbol,
            status="completed",
            verdict=final_state.get("verdict"),
            conviction=final_state.get("verdict_conviction"),
            reasoning=final_state.get("verdict_reasoning"),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {exc}")


# ── Strategy Lab endpoints ──────────────────────────────────────────────────


class StrategyResponse(BaseModel):
    id: str
    name: str
    short_name: str
    type: str
    description: str
    status: str
    pairs: list[str]
    timeframes: list[str]
    agents: list[str]
    entry_logic: str
    exit_logic: str
    color: str
    win_rate: float
    profit_factor: float
    sharpe: float
    max_dd_pct: float
    total_trades: int
    avg_rr: float
    net_pnl_pct: float
    recent_results: list[bool]
    promotion_reasons: list[str]
    proposed_by: str
    created_at: float
    updated_at: float
    mql5_signal_code: str = ""


def _serialise_strategy(rec: Any) -> dict[str, Any]:
    if hasattr(rec, "to_dict"):
        return rec.to_dict()
    return dict(rec)


@app.get("/api/strategies")
async def list_strategies(status: str | None = None) -> dict[str, Any]:
    if _researcher is None:
        raise HTTPException(503, "Research subsystem not initialised")
    records = await _researcher.registry.list(status=status)
    items = [_serialise_strategy(r) for r in records]
    return {
        "items": items,
        "count": len(items),
        "rules": _researcher.registry.rules.__dict__,
    }


@app.get("/api/strategies/{strategy_id}")
async def get_strategy(strategy_id: str) -> dict[str, Any]:
    if _researcher is None:
        raise HTTPException(503, "Research subsystem not initialised")
    rec = await _researcher.registry.get(strategy_id)
    if rec is None:
        raise HTTPException(404, "Strategy not found")
    return _serialise_strategy(rec)


class ResearchTriggerRequest(BaseModel):
    n: int = 2


@app.post("/api/strategies/research")
async def trigger_research(body: ResearchTriggerRequest | None = None) -> dict[str, Any]:
    if _researcher is None:
        raise HTTPException(503, "Research subsystem not initialised")
    n = max(1, min(5, (body.n if body else 2)))
    return await _researcher.run_cycle(n=n)


@app.post("/api/strategies/{strategy_id}/reevaluate")
async def reevaluate_strategy(strategy_id: str) -> dict[str, Any]:
    if _researcher is None:
        raise HTTPException(503, "Research subsystem not initialised")
    rec = await _researcher.registry.get(strategy_id)
    if rec is None:
        raise HTTPException(404, "Strategy not found")
    saved = await _researcher._evaluate_and_save(rec)
    if saved is None:
        raise HTTPException(500, "Re-evaluation failed")
    return _serialise_strategy(saved)


class StatusUpdateRequest(BaseModel):
    status: str


@app.put("/api/strategies/{strategy_id}/status")
async def update_strategy_status(
    strategy_id: str, body: StatusUpdateRequest,
) -> dict[str, Any]:
    if _researcher is None:
        raise HTTPException(503, "Research subsystem not initialised")
    rec = await _researcher.registry.set_status(strategy_id, body.status)
    if rec is None:
        raise HTTPException(404, "Strategy not found")
    return _serialise_strategy(rec)


@app.get("/api/strategies/research/log")
async def research_log(limit: int = 25) -> dict[str, Any]:
    if _researcher is None:
        raise HTTPException(503, "Research subsystem not initialised")
    runs = await _researcher.registry.get_research_log(limit=limit)
    return {"runs": runs, "count": len(runs)}


@app.get("/api/agents/decision/{symbol}")
async def latest_decision(symbol: str) -> dict[str, Any]:
    if _researcher is None:
        raise HTTPException(503, "Research subsystem not initialised")
    payload = await _researcher.registry.get_live_decision(symbol.upper())
    if payload is None:
        raise HTTPException(404, "No decision yet for this symbol")
    return payload


@app.post("/api/agents/brief/generate")
async def trigger_brief_generation(symbol: str = "XAUUSD"):
    """Manually trigger a daily brief generation (bypasses schedule)."""
    try:
        from .daily_brief import generate_brief
        result = await generate_brief(redis_client.r, symbol=symbol)
        return {"status": "ok", "symbol": symbol, "brief": result}
    except Exception as exc:
        logger.exception("brief/generate failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/agents/reasoning/{cycle_id}", response_model=ReasoningResponse)
async def get_reasoning(cycle_id: str):
    cycle = _cycle_store.get(cycle_id)
    if not cycle:
        raise HTTPException(status_code=404, detail=f"Cycle {cycle_id} not found")
    state = cycle.get("state", {})
    return ReasoningResponse(
        cycle_id=cycle_id,
        state={
            k: v for k, v in state.items()
            if k not in ("messages",)
        },
        messages=[
            m if isinstance(m, dict) else {"content": str(m)}
            for m in state.get("messages", [])
        ],
        errors=state.get("errors", []),
    )
