"""
Nexus Risk — FastAPI service (port 8006).

Provides REST endpoints for risk evaluation, circuit-breaker status, and
position sizing.  Also runs a background Redis Stream consumer that
evaluates incoming trade proposals in real time.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
from shared.http_client import new_client
import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from shared.config_loader import ConfigLoader
from shared.constants import (
    CACHE_RISK_STATE,
    RedisStream,
    RiskDecision,
    ServiceName,
)
from shared.events import RiskDecisionEvent, TradeCloseEvent
from shared.models import CircuitBreaker, RiskState, TradeProposal
from shared.nexus_trades import is_nexus_comment, partition_positions
from shared.redis_client import RedisClient
from shared.sessions import detect_session_now
from shared import runtime_settings

import time as _time_mod

from .anti_tilt import AntiTilt, TiltStatus
from .correlation_checker import CorrelationChecker
from .position_sizer import PositionSizer
from .rules_engine import RulesEngine

from shared import setup_logger as _setup_logger
_setup_logger("nexus-risk")
log = structlog.get_logger(__name__)

# ── Globals ───────────────────────────────────────────────────────────────────

redis = RedisClient()
config = ConfigLoader()
rules_engine = RulesEngine(config)
position_sizer = PositionSizer(config)
correlation_checker = CorrelationChecker()
anti_tilt = AntiTilt()

_current_state: dict[str, Any] = {
    "balance": 0.0,
    "equity": 0.0,
    "daily_loss_pct": 0.0,
    "weekly_dd_pct": 0.0,
    "open_positions_count": 0,
    "open_positions": [],
    "total_positions_count": 0,
    "external_positions_count": 0,
    "tilt_status": "NORMAL",
}
_day_start_balance: float = 0.0  # captured on first successful sync each day
_last_sync_day: str = ""         # ISO date of last balance snapshot
_daily_trade_count: int = 0      # trades executed today (reset on new day / manual reset)
_trades_paused: bool = False     # manual pause flag (cleared by reset-day)
_DAILY_COUNT_KEY = "nexus:risk:daily_trade_count"  # persisted in Redis
_DAILY_COUNT_DAY_KEY = "nexus:risk:daily_count_day"
_RECENT_RESULTS_KEY = "nexus:risk:recent_results"  # list of recent pnl values

# ── Nexus-only daily PnL ─────────────────────────────────────────────────────
# The daily-loss breaker must react ONLY to trades this system placed. The MT5
# account is shared (manual trades / other EAs show up as "external"), and on
# 2026-07-16 an external -$1,092 session tripped the 20% breaker and froze
# Nexus for the rest of the day even though Nexus itself was flat (-$0.08).
_NEXUS_DAILY_PNL_KEY = "nexus:risk:nexus_daily_pnl"
_NEXUS_DAILY_PNL_DAY_KEY = "nexus:risk:nexus_daily_pnl_day"


async def _get_nexus_daily_pnl() -> float:
    """Cumulative closed PnL of Nexus-placed trades for the current UTC day."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stored_day = await redis.r.get(_NEXUS_DAILY_PNL_DAY_KEY)
        if stored_day != today:
            return 0.0
        raw = await redis.r.get(_NEXUS_DAILY_PNL_KEY)
        return float(raw) if raw is not None else 0.0
    except Exception:
        return 0.0


async def _add_nexus_daily_pnl(pnl_usd: float) -> None:
    """Accumulate a closed Nexus trade's PnL into today's bucket (day-aware)."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stored_day = await redis.r.get(_NEXUS_DAILY_PNL_DAY_KEY)
        if stored_day != today:
            await redis.r.set(_NEXUS_DAILY_PNL_KEY, "0", ex=172_800)
            await redis.r.set(_NEXUS_DAILY_PNL_DAY_KEY, today, ex=172_800)
        await redis.r.incrbyfloat(_NEXUS_DAILY_PNL_KEY, round(pnl_usd, 2))
    except Exception:
        log.debug("risk.nexus_daily_pnl.update_failed", exc_info=True)
_DAY_START_BALANCE_KEY = "nexus:risk:day_start_balance"  # persisted across restarts
_DAY_START_DAY_KEY = "nexus:risk:day_start_day"          # ISO date for the balance

SERVICE_NAME = os.getenv("NEXUS_SERVICE_NAME", ServiceName.RISK)
SERVICE_PORT = int(os.getenv("NEXUS_SERVICE_PORT", "8006"))
CONSUMER_GROUP = "nexus-risk-group"
CONSUMER_NAME = "nexus-risk-1"
AGGRESSIVE_TRADING = os.getenv("NEXUS_AGGRESSIVE_TRADING", "false").lower() == "true"
EXECUTOR_URL = os.getenv("NEXUS_EXECUTOR_URL", "http://localhost:8007")
ACCOUNT_SYNC_INTERVAL = int(os.getenv("NEXUS_ACCOUNT_SYNC_INTERVAL", "30"))

# Hard session gate — every proposal is checked against the server clock, so
# no caller-supplied state can sneak a trade through at 00:30 rollover again.
# Overridable: NEXUS_ALLOW_OFF_HOURS=true disables the gate entirely.
ALLOW_OFF_HOURS = os.getenv("NEXUS_ALLOW_OFF_HOURS", "false").lower() == "true"
ALLOWED_SESSIONS = {
    s.strip().upper()
    for s in os.getenv("NEXUS_ALLOWED_SESSIONS", "LONDON,NEW_YORK,OVERLAP").split(",")
    if s.strip()
}


# ── Background account-state sync ───────────────────────────────────────────

async def _sync_account_state() -> None:
    """Periodically fetch real account balance from the executor service."""
    global _day_start_balance, _last_sync_day

    log.info(
        "risk.account_sync.started",
        executor_url=EXECUTOR_URL,
        interval=ACCOUNT_SYNC_INTERVAL,
    )

    async with new_client(timeout=10.0) as client:
        while True:
            try:
                resp = await client.get(f"{EXECUTOR_URL}/api/executor/positions")
                resp.raise_for_status()
                data = resp.json()

                account = data.get("account", {})
                balance = float(account.get("balance", 0))
                equity = float(account.get("equity", 0))
                positions = data.get("positions", [])
                nexus_pos, external_pos = partition_positions(positions)

                if balance > 0:
                    _current_state["balance"] = balance
                    _current_state["equity"] = equity
                    _current_state["total_positions_count"] = len(positions)
                    _current_state["external_positions_count"] = len(external_pos)
                    _current_state["open_positions_count"] = len(nexus_pos)
                    _current_state["open_positions"] = nexus_pos

                    # Track daily drawdown
                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    if _last_sync_day != today:
                        global _daily_trade_count
                        _day_start_balance = balance
                        _last_sync_day = today
                        _daily_trade_count = 0
                        # Persist so restarts don't lose the true day-start balance
                        try:
                            await redis.r.set(_DAY_START_BALANCE_KEY, str(balance), ex=172_800)
                            await redis.r.set(_DAY_START_DAY_KEY, today, ex=172_800)
                        except Exception:
                            pass
                        log.info(
                            "risk.account_sync.new_day",
                            day_start_balance=balance,
                        )
                    elif _day_start_balance == 0.0 and balance > 0:
                        # Day count was restored from Redis (so _last_sync_day==today)
                        # but day_start_balance wasn't (first start of the day).
                        # Capture it without resetting the trade count.
                        _day_start_balance = balance
                        try:
                            await redis.r.set(_DAY_START_BALANCE_KEY, str(balance), ex=172_800)
                            await redis.r.set(_DAY_START_DAY_KEY, today, ex=172_800)
                        except Exception:
                            pass
                        log.info(
                            "risk.account_sync.day_start_capture",
                            day_start_balance=balance,
                        )

                    _current_state["day_start_balance"] = _day_start_balance
                    if _day_start_balance > 0:
                        # Daily loss = Nexus-placed trades ONLY (closed today +
                        # floating on open Nexus positions). Account-wide equity
                        # is NOT used: external/manual trades on the shared MT5
                        # account must not trip Nexus's own breaker.
                        closed_nexus_pnl = await _get_nexus_daily_pnl()
                        floating_nexus_pnl = sum(
                            float(p.get("pnl_usd", p.get("pnl", 0)) or 0)
                            for p in nexus_pos
                        )
                        nexus_day_pnl = closed_nexus_pnl + floating_nexus_pnl
                        daily_loss = max(0.0, -nexus_day_pnl) / _day_start_balance * 100
                        _current_state["daily_loss_pct"] = daily_loss
                        _current_state["nexus_daily_pnl"] = round(nexus_day_pnl, 2)

                    log.debug(
                        "risk.account_sync.ok",
                        balance=balance,
                        equity=equity,
                        positions=len(positions),
                        nexus_positions=len(nexus_pos),
                        external_positions=len(external_pos),
                        daily_loss_pct=_current_state["daily_loss_pct"],
                    )
                else:
                    log.warning("risk.account_sync.zero_balance")

            except httpx.ConnectError:
                log.warning("risk.account_sync.executor_unreachable")
            except Exception:
                log.exception("risk.account_sync.error")

            # Refresh the cached risk:state on EVERY sync so the dashboard (which
            # reads the gateway's Redis cache, not this service directly) always
            # sees fresh balance / positions / breakers instead of a stale value
            # that expires after its TTL and renders as all-zeros.
            try:
                await _cache_risk_state()
            except Exception:
                log.debug("risk.account_sync.cache_refresh_failed", exc_info=True)

            await asyncio.sleep(ACCOUNT_SYNC_INTERVAL)


# ── Request / Response Models ─────────────────────────────────────────────────

class EvaluateRequest(BaseModel):
    proposal: TradeProposal
    state: dict[str, Any] = Field(default_factory=dict)


class EvaluateResponse(BaseModel):
    decision: str
    approved_size: float = 0.0
    violations: list[dict[str, Any]] = Field(default_factory=list)
    sizing: dict[str, Any] = Field(default_factory=dict)
    tilt: dict[str, Any] = Field(default_factory=dict)


class RiskStatusResponse(BaseModel):
    state: dict[str, Any]
    tilt: dict[str, Any]
    circuit_breakers: list[dict[str, Any]]


# ── Background Stream Consumer ────────────────────────────────────────────────

async def _risk_stream_consumer() -> None:
    """Consume trade proposals from the agent verdict stream and evaluate."""
    await redis.connect()
    log.info("risk.stream_consumer.started")
    async for event in redis.subscribe_stream(
        RedisStream.AGENT_VERDICT,
        CONSUMER_GROUP,
        CONSUMER_NAME,
    ):
        try:
            # ── Kill switch check ─────────────────────────────────────
            kill_switch = await redis.cache_get("system:kill_switch")
            if kill_switch:
                log.warning(
                    "risk.kill_switch_active",
                    symbol=getattr(event, "symbol", ""),
                )
                reject_event = RiskDecisionEvent(
                    source=SERVICE_NAME,
                    correlation_id=event.correlation_id,
                    proposal_id="",
                    decision=RiskDecision.REJECT,
                    original_size=0,
                    approved_size=0,
                    reason="Kill switch is active",
                    data={},
                )
                await redis.publish_event(RedisStream.RISK_DECISION, reject_event)
                continue

            event_data = event.data if isinstance(event.data, dict) else {}
            already_executed = bool(event_data.get("already_executed"))

            proposal_data = {
                "symbol": getattr(event, "symbol", ""),
                "direction": getattr(event, "verdict", "NO_TRADE"),
                "entry": float(event_data.get("entry", 0)) if event_data.get("entry") else 0,
                "sl": float(event_data.get("sl", 0)) if event_data.get("sl") else 0,
                "tp1": float(event_data.get("tp1", 0)) if event_data.get("tp1") else 0,
                "conviction": float(getattr(event, "conviction", 0)),
            }
            if proposal_data["entry"] == 0 or proposal_data["sl"] == 0:
                continue

            proposal = TradeProposal(**proposal_data)
            result = await _evaluate_proposal(proposal, _current_state)

            skip_execution = already_executed and result["decision"] == RiskDecision.APPROVE
            decision_event = RiskDecisionEvent(
                source=SERVICE_NAME,
                correlation_id=event.correlation_id,
                proposal_id=proposal.id,
                decision=result["decision"],
                original_size=proposal.conviction,
                approved_size=result.get("approved_size", 0),
                reason="; ".join(v.get("message", "") for v in result.get("violations", [])),
                data={
                    "symbol": proposal.symbol,
                    "side": proposal.direction,
                    "direction": proposal.direction,
                    "entry": proposal.entry,
                    "sl": proposal.sl,
                    "tp1": proposal.tp1,
                    "tp": proposal.tp1,
                    "size": float(event_data.get("size", result.get("approved_size", 0)) or 0),
                    "comment": str(event_data.get("comment", "")),
                    "strategy": str(event_data.get("strategy", "genai")),
                    "skip_execution": skip_execution,
                    "already_executed": already_executed,
                    "order_id": str(event_data.get("order_id", "")),
                },
            )
            await redis.publish_event(RedisStream.RISK_DECISION, decision_event)

        except Exception:
            log.exception("risk.stream_consumer.error")


# ── Trade-close stream consumer — feeds anti-tilt + circuit-breaker ─────────

_TRADE_CLOSE_CG = "nexus-risk-tradeclose-cg"
_TRADE_CLOSE_CN = "nexus-risk-tradeclose-1"

# ── Per-direction loss streak (anti-revenge) ─────────────────────────────────
# June 9-11 the system kept re-selling GOLD into a rally — 16 consecutive
# losses, most in the same symbol+direction. After N straight losses on one
# direction, block re-entry on THAT direction for a cooldown window while the
# opposite direction (and other symbols) stay tradeable.

_DIR_LOSS_LIMIT = int(os.getenv("NEXUS_DIR_LOSS_LIMIT", "2"))
_DIR_BLOCK_MINUTES = int(os.getenv("NEXUS_DIR_BLOCK_MINUTES", "60"))
_DIR_LOSS_PREFIX = "nexus:risk:dir_losses:"
_DIR_BLOCK_PREFIX = "nexus:risk:dir_block:"


async def _update_direction_streak(symbol: str, side: str, pnl_usd: float) -> None:
    """Track consecutive losses per symbol+direction; arm the block at the limit."""
    if not symbol or not side:
        return
    dir_key = f"{symbol.upper()}:{side.upper()}"
    try:
        if pnl_usd >= 0:
            await redis.r.delete(_DIR_LOSS_PREFIX + dir_key)
            return
        losses = int(await redis.r.incr(_DIR_LOSS_PREFIX + dir_key))
        await redis.r.expire(_DIR_LOSS_PREFIX + dir_key, 86400)
        if losses >= _DIR_LOSS_LIMIT:
            await redis.r.set(
                _DIR_BLOCK_PREFIX + dir_key,
                str(losses),
                ex=_DIR_BLOCK_MINUTES * 60,
            )
            log.warning(
                "risk.direction_blocked",
                symbol=symbol,
                side=side,
                consecutive_losses=losses,
                block_minutes=_DIR_BLOCK_MINUTES,
            )
    except Exception:
        log.debug("risk.direction_streak_update_failed", exc_info=True)


async def _direction_block_remaining(symbol: str, side: str) -> float:
    """Seconds left in the same-direction re-entry block (0 = not blocked)."""
    try:
        ttl = await redis.r.ttl(f"{_DIR_BLOCK_PREFIX}{symbol.upper()}:{side.upper()}")
        return float(ttl) if ttl and ttl > 0 else 0.0
    except Exception:
        return 0.0


async def _trade_close_consumer() -> None:
    """Listen for closed trades and feed results to anti-tilt & circuit-breaker.

    Previously anti_tilt.update() was NEVER called, so the system ignored 17
    consecutive losses without pausing. This consumer fixes that.
    """
    await redis.connect()
    log.info("risk.trade_close_consumer.started")
    async for event in redis.subscribe_stream(
        RedisStream.TRADE_CLOSE,
        _TRADE_CLOSE_CG,
        _TRADE_CLOSE_CN,
    ):
        try:
            data = event.data if isinstance(event.data, dict) else {}
            pnl_usd = float(getattr(event, "pnl_usd", 0) or data.get("pnl_usd", 0) or 0)
            size = float(getattr(event, "size", 0) or data.get("size", 0.01) or 0.01)
            side = str(getattr(event, "side", "") or data.get("side", ""))
            symbol = getattr(event, "symbol", "") or data.get("symbol", "")

            # 0. Nexus-only gate: the position monitor publishes closes for
            # EVERY position on the account, including manual/other-EA trades
            # (empty or foreign comment). Those must not feed anti-tilt,
            # loss streaks, recent-results or the daily-loss accumulator —
            # this system's limits apply to this system's trades only.
            comment = str(data.get("source", "") or "")
            if not is_nexus_comment(comment):
                log.info(
                    "risk.trade_close.external_ignored",
                    symbol=symbol,
                    side=side,
                    pnl_usd=pnl_usd,
                )
                continue

            # 0b. Accumulate today's Nexus closed PnL (drives daily_loss_pct)
            await _add_nexus_daily_pnl(pnl_usd)

            # 1. Feed the anti-tilt detector
            status = anti_tilt.update({"pnl_usd": pnl_usd, "size": size})
            log.info(
                "risk.trade_close.anti_tilt_updated",
                pnl_usd=pnl_usd,
                tilt_status=status.value,
                consecutive_losses=anti_tilt.get_state_dict().get("consecutive_losses", 0),
            )
            await anti_tilt.persist_to_redis(redis.r)

            # 2. Persist recent results for circuit-breaker (last 20 trades)
            try:
                result_entry = f"{pnl_usd:.2f}"
                await redis.r.lpush(_RECENT_RESULTS_KEY, result_entry)
                await redis.r.ltrim(_RECENT_RESULTS_KEY, 0, 19)  # keep last 20
            except Exception:
                log.debug("risk.trade_close.redis_persist_failed", exc_info=True)

            # 2b. Per-direction loss streak → re-entry block (anti-revenge)
            await _update_direction_streak(symbol, side, pnl_usd)

            # 3. Activate kill switch if tilt detected
            if status == TiltStatus.TILT:
                log.warning(
                    "risk.trade_close.kill_switch_activated",
                    consecutive_losses=anti_tilt.get_state_dict().get("consecutive_losses", 0),
                )
                await redis.cache_set("system:kill_switch", {"reason": "anti_tilt"}, ttl_sec=1800)

            await _cache_risk_state()

        except Exception:
            log.exception("risk.trade_close_consumer.error")


async def _restore_daily_count() -> None:
    """Restore the daily trade count from Redis on service startup.

    This prevents the count from resetting to 0 when run.py respawns the
    service, which previously allowed 16+ trades/day despite a 5/day cap.
    """
    global _daily_trade_count, _last_sync_day
    try:
        stored_day = await redis.r.get(_DAILY_COUNT_DAY_KEY)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if stored_day and stored_day == today:
            stored_count = await redis.r.get(_DAILY_COUNT_KEY)
            if stored_count is not None:
                _daily_trade_count = int(stored_count)
                _last_sync_day = today
                log.info(
                    "risk.daily_count.restored",
                    daily_trade_count=_daily_trade_count,
                    day=today,
                )
        else:
            log.info("risk.daily_count.new_day_or_missing", stored_day=stored_day, today=today)
    except Exception:
        log.debug("risk.daily_count.restore_failed", exc_info=True)


async def _persist_daily_count() -> None:
    """Write the current daily trade count to Redis so it survives restarts."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await redis.r.set(_DAILY_COUNT_KEY, str(_daily_trade_count), ex=86400)
        await redis.r.set(_DAILY_COUNT_DAY_KEY, today, ex=86400)
    except Exception:
        pass


async def _restore_day_start_balance() -> None:
    """Restore day_start_balance from Redis so restarts don't break daily loss calc."""
    global _day_start_balance, _last_sync_day
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stored_day = await redis.r.get(_DAY_START_DAY_KEY)
        if stored_day and stored_day == today:
            stored_val = await redis.r.get(_DAY_START_BALANCE_KEY)
            if stored_val:
                _day_start_balance = float(stored_val)
                if not _last_sync_day:
                    _last_sync_day = today
                log.info(
                    "risk.day_start_balance.restored",
                    day_start_balance=_day_start_balance,
                    day=today,
                )
    except Exception:
        log.debug("risk.day_start_balance.restore_failed", exc_info=True)


async def _check_circuit_breaker() -> dict | None:
    """Check recent-trade circuit breaker: if last 10 trades have <20% WR, pause.

    Returns a violation dict if triggered, None otherwise.
    """
    try:
        raw = await redis.r.lrange(_RECENT_RESULTS_KEY, 0, 9)
        if not raw or len(raw) < 5:
            return None  # not enough data

        results = [float(x) for x in raw]
        wins = sum(1 for r in results if r > 0)
        wr = wins / len(results)

        if wr < 0.20 and len(results) >= 8:
            log.warning(
                "risk.circuit_breaker.low_win_rate",
                win_rate=wr,
                last_n=len(results),
                results=results,
            )
            return {
                "rule": "circuit_breaker_win_rate",
                "message": (
                    f"Circuit breaker: win rate {wr:.0%} on last {len(results)} trades. "
                    f"System paused — needs manual reset-day."
                ),
                "current_value": wr,
                "limit_value": 0.20,
            }
    except Exception:
        log.debug("risk.circuit_breaker.check_failed", exc_info=True)
    return None


_ERGODIC_PAUSE_KEY = "nexus:risk:ergodic_pause_until"
_INST_KEY_TPL = "nexus:features:institutional:{symbol}"


async def _get_ergodic_lot_multiplier(symbol: str, base_lot: float) -> tuple[float, dict]:
    """
    Reads pre-computed institutional features and returns a (multiplier, info) tuple.
    multiplier = matrix_profile.position_size_mult × change_points.ensemble_weight
                 × (0.5 if temporal_growth says REDUCE_SIZE_50PCT)

    Falls back to 1.0 if institutional features not yet available — never blocks.
    """
    try:
        raw = await redis.r.get(_INST_KEY_TPL.format(symbol=symbol))
        if not raw:
            return 1.0, {"source": "no_institutional_data"}
        import json as _json
        inst = _json.loads(raw)
        mp_mult = float(inst.get("matrix_profile", {}).get("position_size_mult", 1.0))
        cp_mult = float(inst.get("change_points", {}).get("ensemble_weight", 1.0))
        tg_action = inst.get("temporal_growth", {}).get("action", "NORMAL")
        tg_mult = 0.5 if tg_action == "REDUCE_SIZE_50PCT" else 1.0
        combined = mp_mult * cp_mult * tg_mult
        ergodic_lot = inst.get("ergodic_kelly", {}).get("lot", base_lot)
        return combined, {
            "source": "ergodic",
            "mp_mult": mp_mult,
            "cp_mult": cp_mult,
            "tg_mult": tg_mult,
            "combined_mult": combined,
            "ergodic_lot_suggestion": ergodic_lot,
            "temporal_action": tg_action,
        }
    except Exception:
        return 1.0, {"source": "error_fallback"}


async def _check_ergodic_circuit_breaker(symbol: str = "XAUUSD") -> dict | None:
    """
    Circuit breaker basado en tasa de crecimiento temporal g (Peters 2019).
    Se dispara antes que el daily_loss_limit del 20% — detecta destrucción
    de capital estadística antes de que sea severa.

    PAUSE_SYSTEM   → g < -0.003 → pausar 30 minutos
    REDUCE_SIZE    → g < 0      → no bloquea, ajuste de tamaño (via _get_ergodic_lot_multiplier)
    """
    try:
        # Check active pause
        pause_raw = await redis.r.get(_ERGODIC_PAUSE_KEY)
        if pause_raw:
            remaining = float(pause_raw) - _time_mod.time()
            if remaining > 0:
                return {
                    "rule": "ergodic_temporal_growth",
                    "message": (
                        f"Ergodic circuit breaker active: g < -0.003. "
                        f"Resume in {remaining/60:.0f}min."
                    ),
                    "current_value": 0.0,
                    "limit_value": -0.003,
                }

        raw = await redis.r.get(_INST_KEY_TPL.format(symbol=symbol))
        if not raw:
            return None
        import json as _json
        inst = _json.loads(raw)
        tg = inst.get("temporal_growth", {})
        if tg.get("action") == "PAUSE_SYSTEM":
            pause_until = _time_mod.time() + 1800
            await redis.r.set(_ERGODIC_PAUSE_KEY, str(pause_until), ex=1800)
            g_val = tg.get("temporal_growth_g", 0.0)
            log.warning(
                "risk.ergodic_circuit_breaker.fired",
                g=g_val, symbol=symbol, duration_min=30,
            )
            return {
                "rule": "ergodic_temporal_growth",
                "message": (
                    f"Ergodic circuit breaker: temporal growth g={g_val:.4f} < -0.003. "
                    f"Capital destruction detected. Paused 30 minutes."
                ),
                "current_value": float(g_val),
                "limit_value": -0.003,
            }
    except Exception:
        log.debug("ergodic_circuit_breaker.check_failed", exc_info=True)
    return None


_last_trade_ts_by_dir: dict[str, float] = {}   # in-memory fallback when Redis is down
_STACK_COOLDOWN_SEC = int(os.getenv("NEXUS_STACK_COOLDOWN_SEC", "120"))
_STACK_KEY_PREFIX = "nexus:risk:last_trade:"


async def _stack_cooldown_remaining(dir_key: str) -> float:
    """Seconds left in the same-direction cooldown.

    Redis-backed so the cooldown survives service restarts (a respawn used to
    reset the in-memory dict and allow immediate same-direction stacking).
    """
    try:
        ttl = await redis.r.ttl(_STACK_KEY_PREFIX + dir_key)
        if ttl and ttl > 0:
            return float(ttl)
    except Exception:
        pass
    last = _last_trade_ts_by_dir.get(dir_key, 0.0)
    if not last:
        return 0.0
    elapsed = _time_mod.monotonic() - last
    return max(0.0, _STACK_COOLDOWN_SEC - elapsed)


async def _mark_stack_cooldown(dir_key: str) -> None:
    _last_trade_ts_by_dir[dir_key] = _time_mod.monotonic()
    try:
        await redis.r.set(_STACK_KEY_PREFIX + dir_key, "1", ex=_STACK_COOLDOWN_SEC)
    except Exception:
        log.debug("risk.stack_cooldown.persist_failed", exc_info=True)


async def _evaluate_proposal(
    proposal: TradeProposal,
    state: dict[str, Any],
) -> dict[str, Any]:
    global _daily_trade_count
    merged = {**_current_state, **state}

    # ── Tilt check — BLOCK if anti-tilt detected emotional trading ──────
    tilt_status = anti_tilt.get_tilt_status()
    if tilt_status == TiltStatus.TILT:
        return {
            "decision": RiskDecision.REJECT,
            "approved_size": 0.0,
            "violations": [{
                "rule": "anti_tilt",
                "message": (
                    f"Anti-tilt active: {anti_tilt.get_state_dict().get('consecutive_losses', 0)} "
                    f"consecutive losses. Cooldown {anti_tilt.get_cooldown_remaining():.0f}min remaining."
                ),
            }],
            "sizing": {},
            "tilt": anti_tilt.get_state_dict(),
        }

    # ── Hard session gate (server clock, not caller-supplied state) ─────
    if not ALLOW_OFF_HOURS:
        _session_now = detect_session_now()
        if _session_now.value not in ALLOWED_SESSIONS:
            return {
                "decision": RiskDecision.REJECT,
                "approved_size": 0.0,
                "violations": [{
                    "rule": "session_gate",
                    "message": (
                        f"Session {_session_now.value} not in allowed set "
                        f"{sorted(ALLOWED_SESSIONS)} (set NEXUS_ALLOWED_SESSIONS "
                        f"or NEXUS_ALLOW_OFF_HOURS=true to change)"
                    ),
                }],
                "sizing": {},
                "tilt": anti_tilt.get_state_dict(),
            }

    # ── Circuit-breaker: low win rate on last N trades → pause ──────────
    cb_violation = await _check_circuit_breaker()
    if cb_violation:
        return {
            "decision": RiskDecision.REJECT,
            "approved_size": 0.0,
            "violations": [cb_violation],
            "sizing": {},
            "tilt": anti_tilt.get_state_dict(),
        }

    # ── Ergodic circuit breaker: temporal growth g (Peters 2019) ────────
    ergodic_cb = await _check_ergodic_circuit_breaker(proposal.symbol)
    if ergodic_cb:
        return {
            "decision": RiskDecision.REJECT,
            "approved_size": 0.0,
            "violations": [ergodic_cb],
            "sizing": {},
            "tilt": anti_tilt.get_state_dict(),
        }

    # ── Anti-stacking guard ──────────────────────────────────────────────
    # The account-state sync runs every 30s, so back-to-back proposals can each
    # see "0 open positions" and stack 3 trades in the same direction before the
    # first one is reflected. Enforce a per-(symbol,direction) cooldown so we
    # never pile onto the same bet within the window.
    _dir_key = f"{proposal.symbol.upper()}:{str(proposal.direction).upper()}"
    _cooldown_left = await _stack_cooldown_remaining(_dir_key)
    if _cooldown_left > 0:
        return {
            "decision": RiskDecision.REJECT,
            "approved_size": 0.0,
            "violations": [{
                "rule": "anti_stacking",
                "message": (
                    f"Same-direction {proposal.direction} on {proposal.symbol} "
                    f"within cooldown ({_cooldown_left:.0f}s of "
                    f"{_STACK_COOLDOWN_SEC}s remaining)"
                ),
            }],
            "sizing": {},
            "tilt": anti_tilt.get_state_dict(),
        }

    # ── Anti-revenge: direction blocked after consecutive losses ────────
    _block_left = await _direction_block_remaining(proposal.symbol, str(proposal.direction))
    if _block_left > 0:
        return {
            "decision": RiskDecision.REJECT,
            "approved_size": 0.0,
            "violations": [{
                "rule": "direction_loss_block",
                "message": (
                    f"{proposal.direction} on {proposal.symbol} blocked after "
                    f"{_DIR_LOSS_LIMIT}+ consecutive losses "
                    f"({_block_left / 60:.0f}min remaining)"
                ),
            }],
            "sizing": {},
            "tilt": anti_tilt.get_state_dict(),
        }

    if merged.get("balance", 0) == 0:
        log.warning(
            "risk.evaluate.balance_not_synced",
            symbol=proposal.symbol,
            msg="Balance is 0 — account sync may not have completed yet",
        )

    # ── Apply front-configurable runtime settings (daily loss %, caps) ──
    settings = await runtime_settings.get_settings(redis)
    rules_engine.apply_runtime_overrides(settings)

    # Manual pause flag — when set, reject everything until reset-day.
    if _trades_paused:
        return {
            "decision": RiskDecision.REJECT,
            "approved_size": 0.0,
            "violations": [{"rule": "manual_pause", "message": "Trading manually paused"}],
            "sizing": {},
            "tilt": anti_tilt.get_state_dict(),
        }

    # Daily trade cap (front-configurable). 0/absent = unlimited.
    max_daily_trades = int(settings.get("max_daily_trades", 0) or 0)
    if max_daily_trades > 0 and _daily_trade_count >= max_daily_trades:
        return {
            "decision": RiskDecision.REJECT,
            "approved_size": 0.0,
            "violations": [{
                "rule": "daily_trade_limit",
                "message": f"Daily trade limit reached ({_daily_trade_count}/{max_daily_trades})",
                "current_value": _daily_trade_count,
                "limit_value": max_daily_trades,
            }],
            "sizing": {},
            "tilt": anti_tilt.get_state_dict(),
        }

    passed, violations = rules_engine.check_all_rules(proposal, merged)
    violation_dicts = [
        {
            "rule": v.rule,
            "message": v.message,
            "severity": v.severity,
            "current_value": v.current_value,
            "limit_value": v.limit_value,
        }
        for v in violations
    ]

    if not passed:
        return {
            "decision": RiskDecision.REJECT,
            "approved_size": 0.0,
            "violations": violation_dicts,
            "sizing": {},
            "tilt": anti_tilt.get_state_dict(),
        }

    # Front-configurable risk-per-trade (% of equity), still capped by the
    # rules-engine hardcoded max_risk_per_trade floor via check_all_rules above.
    risk_cap = float(settings.get("risk_per_trade_pct", 1.0))
    # Trade memory stats for Kelly + profit-edge sizing
    win_rate, avg_win, avg_loss, edge_mult = 0.0, 0.0, 0.0, 1.0
    try:
        import json as _json
        raw_metrics = await redis.cache_get("nexus:trade_memory:metrics", prefix=False)
        if raw_metrics and isinstance(raw_metrics, dict):
            overall = raw_metrics.get("overall", {})
            win_rate = float(overall.get("win_rate", 0) or 0)
            avg_win = float(overall.get("avg_win_pips", 0) or 0)
            avg_loss = float(overall.get("avg_loss_pips", 0) or 0)
            net_pnl = float(overall.get("net_pnl_usd", 0) or 0)
            if net_pnl > 0 and win_rate >= 0.55:
                edge_mult = min(1.5, 1.0 + (win_rate - 0.5))
            elif net_pnl < 0:
                edge_mult = max(0.6, 1.0 + net_pnl / max(merged.get("balance", 1000), 1) * 10)
    except Exception:
        pass

    sizing = position_sizer.calculate_size(
        balance=merged.get("balance", 10_000),
        risk_pct=min(proposal.conviction * 2.5, risk_cap),
        entry=proposal.entry,
        sl=proposal.sl,
        vol_regime=merged.get("vol_regime", "medium"),
        tilt_status=anti_tilt.get_tilt_status().value.lower(),
        session=merged.get("session", "london"),
        symbol=proposal.symbol,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_edge_mult=edge_mult,
    )

    adjusted_size = position_sizer.scale_by_conviction(sizing.lots, proposal.conviction)
    # Conviction scaling (×1.15 at high conviction) runs AFTER the sizer's cap —
    # re-clamp so no approved size can ever exceed the balance/hard-ceiling lots.
    adjusted_size = round(
        min(adjusted_size, position_sizer.max_allowed_lots(merged.get("balance", 10_000))),
        2,
    )

    # ── Ergodic lot multiplier (Peters 2019) ────────────────────────────
    ergodic_mult, ergodic_info = await _get_ergodic_lot_multiplier(
        proposal.symbol, adjusted_size
    )
    if ergodic_mult < 1.0:
        pre_ergodic = adjusted_size
        adjusted_size = round(
            max(0.01, min(adjusted_size * ergodic_mult,
                          position_sizer.max_allowed_lots(merged.get("balance", 10_000)))),
            2,
        )
        log.info(
            "risk.ergodic_size_adjusted",
            symbol=proposal.symbol,
            before=pre_ergodic,
            after=adjusted_size,
            mult=ergodic_mult,
        )

    # Count this approval toward the daily trade cap + record for anti-stacking.
    _daily_trade_count += 1
    await _mark_stack_cooldown(_dir_key)
    # Persist count to Redis so it survives service restarts (run.py respawn).
    asyncio.ensure_future(_persist_daily_count())

    return {
        "decision": RiskDecision.APPROVE,
        "approved_size": adjusted_size,
        "violations": violation_dicts,
        "sizing": {
            "lots": adjusted_size,
            "raw_lots": sizing.lots,
            "risk_pct_used": sizing.risk_pct_used,
            "kelly_fraction": sizing.kelly_fraction,
            "vol_factor": sizing.vol_factor,
            "tilt_factor": sizing.tilt_factor,
            "session_factor": sizing.session_factor,
            "capped": sizing.capped,
            "ergodic": ergodic_info,
        },
        "tilt": anti_tilt.get_state_dict(),
    }


# ── Cache risk state to Redis ────────────────────────────────────────────────

async def _cache_risk_state() -> None:
    tilt = anti_tilt.get_state_dict()
    breakers = rules_engine.get_active_circuit_breakers(_current_state)
    cfg = rules_engine._cfg
    open_count = int(_current_state.get("open_positions_count", 0))
    state = RiskState(
        balance=_current_state.get("balance", 0.0),
        equity=_current_state.get("equity", 0.0),
        daily_loss_pct=_current_state.get("daily_loss_pct", 0.0),
        weekly_dd_pct=_current_state.get("weekly_dd_pct", 0.0),
        open_positions=open_count,
        correlation_exposure=_current_state.get("correlation_exposure", 0.0),
        tilt_status=tilt["status"],
        circuit_breakers=[CircuitBreaker(**b) for b in breakers],
        # Live enforced limits so the dashboard shows the real config (20% daily,
        # 25% weekly, etc.) instead of its hardcoded fallbacks.
        max_daily_loss_pct=cfg.max_daily_loss_pct,
        max_weekly_drawdown_pct=cfg.max_weekly_drawdown_pct,
        open_positions_count=open_count,
        max_positions=cfg.max_open_positions,
        max_correlation=cfg.max_correlation,
        consecutive_losses=int(tilt.get("consecutive_losses", 0)),
        current_session=str(_current_state.get("current_session", "")),
    )
    await redis.cache_set(
        CACHE_RISK_STATE,
        state.model_dump(mode="json"),
        ttl_sec=90,  # > sync interval (30s) so the cache never gaps to zeros
        prefix=False,
    )


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis.connect()
    # Restore persistent state that survives service restarts
    await _restore_daily_count()
    await _restore_day_start_balance()
    await anti_tilt.restore_from_redis(redis.r)
    log.info("risk.service.started", port=SERVICE_PORT)
    consumer_task = asyncio.create_task(_risk_stream_consumer())
    sync_task = asyncio.create_task(_sync_account_state())
    close_task = asyncio.create_task(_trade_close_consumer())
    try:
        yield
    finally:
        for task in (consumer_task, sync_task, close_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await redis.disconnect()
        log.info("risk.service.stopped")


# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Nexus Risk",
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    redis_ok = await redis.health_check()
    return {
        "service": SERVICE_NAME,
        "status": "healthy" if redis_ok else "degraded",
        "redis": redis_ok,
        "tilt_status": anti_tilt.get_tilt_status().value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/risk/status", response_model=RiskStatusResponse)
async def get_risk_status():
    tilt = anti_tilt.get_state_dict()
    breakers = rules_engine.get_active_circuit_breakers(_current_state)
    await _cache_risk_state()
    return RiskStatusResponse(
        state=_current_state,
        tilt=tilt,
        circuit_breakers=breakers,
    )


@app.get("/api/risk/exposure")
async def get_exposure():
    """Exposure snapshot — only Nexus-tagged positions count toward limits."""
    return {
        "daily_loss_pct": _current_state.get("daily_loss_pct", 0.0),
        "weekly_dd_pct": _current_state.get("weekly_dd_pct", 0.0),
        "open_positions": _current_state.get("open_positions_count", 0),
        "positions": _current_state.get("open_positions", []),
        "total_positions": _current_state.get("total_positions_count", 0),
        "external_positions": _current_state.get("external_positions_count", 0),
        "consecutive_losses": anti_tilt.get_state_dict().get("consecutive_losses", 0),
        "tilt_status": anti_tilt.get_tilt_status().value,
        "daily_pnl": 0.0,
        "correlation_exposure": 0.0,
    }


@app.get("/api/risk/circuit-breakers")
async def get_circuit_breakers():
    breakers = rules_engine.get_active_circuit_breakers(_current_state)
    return {
        "circuit_breakers": breakers,
        "any_triggered": len(breakers) > 0,
        "count": len(breakers),
    }


@app.post("/api/risk/evaluate", response_model=EvaluateResponse)
async def evaluate_trade(req: EvaluateRequest):
    try:
        result = await _evaluate_proposal(req.proposal, req.state)
        await _cache_risk_state()
        return EvaluateResponse(**result)
    except Exception as exc:
        log.exception("risk.evaluate.error")
        raise HTTPException(status_code=500, detail=str(exc))


# ── Front-configurable settings + day reset ─────────────────────────────────

class SettingsPatch(BaseModel):
    """Partial update of runtime settings (only provided keys change)."""
    risk_per_trade_pct: float | None = None
    max_daily_loss_pct: float | None = None
    max_weekly_dd_pct: float | None = None
    max_open_positions: int | None = None
    max_daily_trades: int | None = None
    min_hold_minutes: int | None = None
    max_hold_minutes: int | None = None
    min_conviction_override: float | None = None
    research_min_candidates: int | None = None
    research_max_search_rounds: int | None = None
    research_interval_sec: int | None = None
    require_mtf_confirmation: bool | None = None
    min_mtf_agreement: int | None = None


@app.get("/api/risk/settings")
async def get_settings_endpoint():
    """Return the current front-configurable runtime settings + live counters."""
    settings = await runtime_settings.get_settings(redis)
    return {
        "settings": settings,
        "daily_trade_count": _daily_trade_count,
        "trades_paused": _trades_paused,
        "daily_loss_pct": _current_state.get("daily_loss_pct", 0.0),
        "day_start_balance": _day_start_balance,
    }


@app.post("/api/risk/settings")
async def update_settings_endpoint(patch: SettingsPatch):
    """Update runtime settings from the dashboard. Only provided keys change."""
    provided = {k: v for k, v in patch.model_dump().items() if v is not None}
    merged = await runtime_settings.update_settings(provided, redis)
    rules_engine.apply_runtime_overrides(merged)
    log.info("risk.settings.updated", changed=list(provided.keys()))
    return {"settings": merged, "changed": list(provided.keys())}


@app.get("/api/exposure")
async def get_exposure():
    """Portfolio exposure snapshot for the GenAI risk manager (A8).

    A8's tools/risk_tools.check_exposure() has always called this path, but
    the endpoint never existed — every call 404'd and fell back to all-zeros,
    so A8's daily-loss / weekly-DD / max-positions breakers were inert.
    Serving it from _current_state also means A8 automatically inherits the
    Nexus-only daily-loss accounting.
    """
    return {
        "daily_loss_pct": _current_state.get("daily_loss_pct", 0.0),
        "weekly_dd_pct": _current_state.get("weekly_dd_pct", 0.0),
        "open_positions": _current_state.get("open_positions_count", 0),
        "positions": _current_state.get("open_positions", []),
        "consecutive_losses": anti_tilt.get_state_dict().get("consecutive_losses", 0),
        "tilt_status": _current_state.get("tilt_status", "NORMAL"),
        "daily_pnl": _current_state.get("nexus_daily_pnl", 0.0),
        "correlation_exposure": _current_state.get("correlation_exposure", 0.0),
    }


@app.post("/api/risk/reset-day")
async def reset_day():
    """Reset the daily risk budget so trades resume even after limits were hit.

    Re-baselines the day-start balance to the current equity (daily loss → 0),
    clears the daily trade counter, clears the manual pause flag, and clears any
    kill switch. Use this to 'start a fresh day' on demand.
    """
    global _day_start_balance, _daily_trade_count, _trades_paused, _last_sync_day
    equity = _current_state.get("equity", 0.0) or _current_state.get("balance", 0.0)
    if equity > 0:
        _day_start_balance = equity
        # Persist the re-baselined value — without this, the next service
        # restart restores the STALE pre-reset baseline from Redis, and a
        # 20% breaker on an inflated denominator allows far larger absolute
        # losses than the account can actually afford.
        try:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            await redis.r.set(_DAY_START_BALANCE_KEY, str(equity), ex=172_800)
            await redis.r.set(_DAY_START_DAY_KEY, today_str, ex=172_800)
        except Exception:
            log.debug("risk.reset_day.day_start_persist_failed", exc_info=True)
    _current_state["day_start_balance"] = _day_start_balance
    _current_state["daily_loss_pct"] = 0.0
    _current_state["weekly_dd_pct"] = 0.0
    _daily_trade_count = 0
    _trades_paused = False
    _last_sync_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        anti_tilt.reset()
    except Exception:
        pass
    try:
        await redis.cache_delete("system:kill_switch")
    except Exception:
        pass
    # Clear circuit-breaker results + persist zeroed count
    try:
        await redis.r.delete(_RECENT_RESULTS_KEY)
        await _persist_daily_count()
    except Exception:
        pass
    # Full clean slate: Nexus daily-PnL accumulator, per-direction re-entry
    # blocks/streaks, and the ergodic pause — "reset day" from the dashboard
    # must leave nothing armed.
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await redis.r.set(_NEXUS_DAILY_PNL_KEY, "0", ex=172_800)
        await redis.r.set(_NEXUS_DAILY_PNL_DAY_KEY, today, ex=172_800)
        _current_state["nexus_daily_pnl"] = 0.0
        for pattern in (f"{_DIR_LOSS_PREFIX}*", f"{_DIR_BLOCK_PREFIX}*"):
            async for key in redis.r.scan_iter(pattern):
                await redis.r.delete(key)
        await redis.r.delete(_ERGODIC_PAUSE_KEY)
    except Exception:
        log.debug("risk.reset_day.extra_clears_failed", exc_info=True)
    await _cache_risk_state()
    log.info("risk.reset_day", new_day_start_balance=_day_start_balance)
    return {
        "status": "ok",
        "message": "Daily risk budget reset — trading resumed",
        "day_start_balance": _day_start_balance,
        "daily_trade_count": _daily_trade_count,
    }


@app.post("/api/risk/pause")
async def pause_trading(paused: bool = True):
    """Manually pause or resume trading (separate from the daily limits)."""
    global _trades_paused
    _trades_paused = bool(paused)
    return {"status": "ok", "trades_paused": _trades_paused}
