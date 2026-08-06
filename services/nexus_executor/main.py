"""
Nexus Executor — FastAPI service (port 8007).

Provides REST endpoints for order execution, position management, and
a background Redis Stream consumer that processes approved risk decisions.
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from shared.config_loader import ConfigLoader
from shared.constants import RedisStream, ServiceName
from shared.events import (
    OrderEvent,
    PositionUpdateEvent,
    TradeCloseEvent,
    parse_event,
)
from shared.asset_specs import price_to_pips
from shared.nexus_trades import (
    normalize_nexus_comment,
    partition_positions,
    strategy_from_comment,
)
from shared.redis_client import RedisClient

from .broker_adapter import (
    AccountInfo,
    BrokerAdapter,
    OrderRequest,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
)
from .ccxt_executor import CCXTExecutor
from .ibkr_executor import IBKRExecutor
from .mt5_executor import MT5Executor
from .smart_routing import SmartRouter

from shared import setup_logger as _setup_logger
_setup_logger("nexus-executor")
log = structlog.get_logger(__name__)

# ── Globals ───────────────────────────────────────────────────────────────────

redis = RedisClient()
config = ConfigLoader()
smart_router = SmartRouter()

SERVICE_NAME = os.getenv("NEXUS_SERVICE_NAME", ServiceName.EXECUTOR)
SERVICE_PORT = int(os.getenv("NEXUS_SERVICE_PORT", "8007"))
DEFAULT_BROKER = os.getenv("NEXUS_DEFAULT_BROKER", os.getenv("DEFAULT_BROKER", "mt5"))
CONSUMER_GROUP = "nexus-executor-group"
CONSUMER_NAME = "nexus-executor-1"
DEDUP_TTL_SEC = int(os.getenv("NEXUS_ORDER_DEDUP_TTL_SEC", "60"))


# ── Order Deduplication ───────────────────────────────────────────────────────

async def _mark_order_executed(symbol: str, side: str) -> None:
    """Record that an order was just placed so the other execution path skips it."""
    key = f"nexus:recent_orders:{symbol.upper()}:{side.upper()}"
    await redis.r.set(key, "1", ex=DEDUP_TTL_SEC)


async def _fetch_last_price(symbol: str, side: str) -> float:
    """Best-effort last price when a position vanishes without a fill quote."""
    import httpx

    data_url = os.getenv("NEXUS_DATA_URL", "http://localhost:8001")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{data_url}/api/data/ticks/{symbol}",
                params={"count": 1, "source": "cache"},
            )
            if resp.status_code == 200:
                ticks = resp.json()
                if ticks:
                    t = ticks[0]
                    return float(t.get("bid" if side == "BUY" else "ask", 0) or t.get("last", 0))
    except Exception:
        pass
    return 0.0


async def _is_duplicate_order(symbol: str, side: str) -> bool:
    """Return True if this symbol+direction was already executed recently."""
    key = f"nexus:recent_orders:{symbol.upper()}:{side.upper()}"
    return await redis.r.exists(key) > 0


# ── Anti-stacking: one signal = one open position per symbol+direction ───────
# The 60s dedup above only stops bursts; without this check the system can
# re-add to the same bet every TTL window (June 9-11: clusters of 6-7
# identical positions). Capped at the broker level so EVERY execution path
# (REST, stream consumer, agents) is covered.

MAX_PER_DIRECTION = int(os.getenv("NEXUS_MAX_PER_DIRECTION", "1"))
TRADE_META_TTL_SEC = int(os.getenv("NEXUS_TRADE_META_TTL_SEC", str(7 * 24 * 3600)))


def _symbol_names(symbol: str) -> set[str]:
    """All spellings of a symbol: logical + broker alias (MT5_SYMBOL_ALIASES)."""
    logical = symbol.upper().replace("/", "")
    names = {logical}
    for part in os.getenv("MT5_SYMBOL_ALIASES", "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        k = key.strip().upper().replace("/", "")
        v = value.strip().upper()
        if logical in (k, v):
            names.update({k, v})
    return names


async def _open_same_direction_count(symbol: str, side: str) -> int:
    """Count open Nexus positions on this symbol+direction (alias-aware)."""
    names = _symbol_names(symbol)
    broker = await _get_broker()
    positions = await broker.get_positions()
    nexus_pos, _ = partition_positions(positions)
    return sum(
        1
        for p in nexus_pos
        if str(p.get("symbol", "")).upper() in names
        and str(p.get("side", "")).upper() == side.upper()
    )


async def _stacking_blocked(symbol: str, side: str) -> bool:
    """True when MAX_PER_DIRECTION Nexus positions already exist same-direction.

    Fails open (False) if the broker is unreachable so a positions glitch
    cannot freeze trading entirely — the Redis dedup still applies then.
    """
    try:
        return await _open_same_direction_count(symbol, side) >= MAX_PER_DIRECTION
    except Exception:
        log.exception("executor.stacking_check_failed", symbol=symbol, side=side)
        return False


# ── Last-line lot cap at the broker gateway ──────────────────────────────────
# The risk service sizes its own proposals, but manual/dashboard orders and any
# future path land here with an arbitrary ``size`` (a 1.0-lot order slipped
# through on June 10 and lost $410 in one trade). Clamp against the same
# risk_rules.yaml capital caps so no order can exceed them.

def _lot_caps() -> tuple[float, float]:
    """(hard_ceiling_lots, equity_divisor) from risk_rules.yaml → capital."""
    try:
        capital = config._load_yaml("risk_rules.yaml").get("capital", {})
        return (
            float(capital.get("lot_cap_hard_ceiling", 0.50)),
            float(capital.get("lot_cap_equity_divisor", 40_000)),
        )
    except Exception:
        return 0.50, 40_000.0


def _fixed_lots_map() -> dict[str, float]:
    """Per-symbol fixed lot overrides from NEXUS_FIXED_LOTS.

    Format: ``SYMBOL=lots;SYMBOL=lots`` (e.g. ``BTCUSD=0.02``). The
    equity-derived cap is calibrated for gold-sized instruments; a symbol whose
    per-lot risk is much smaller (BTC's M5 ATR is ~$31, so 0.02 lots risks
    <1% of a small account) would otherwise be clamped below its intended size.
    """
    out: dict[str, float] = {}
    for part in os.getenv("NEXUS_FIXED_LOTS", "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        try:
            out[key.strip().upper().replace("/", "")] = float(value)
        except ValueError:
            continue
    return out


async def _clamp_lot_size(size: float, symbol: str = "") -> float:
    hard_ceiling, divisor = _lot_caps()

    # A per-symbol fixed lot wins over the equity heuristic, but is still
    # bounded by the absolute hard ceiling from risk_rules.yaml.
    fixed = _fixed_lots_map().get(symbol.strip().upper().replace("/", "")) if symbol else None
    if fixed is not None:
        lots = max(0.01, min(fixed, hard_ceiling))
        if abs(lots - size) > 1e-9:
            log.info("executor.lot_size_fixed", symbol=symbol, requested=size, fixed=lots)
        return lots

    cap = hard_ceiling
    try:
        broker = await _get_broker()
        account = await broker.get_account_info()
        if account.equity > 0:
            cap = min(cap, max(0.01, round(account.equity / divisor, 2)))
    except Exception:
        pass
    if size > cap:
        log.warning("executor.lot_size_clamped", requested=size, cap=cap)
        return cap
    return size


# ── Trade metadata (strategy/conviction/session) for close-time telemetry ────

_strategy_from_comment = strategy_from_comment


async def _current_regime() -> str:
    """Read the live regime from the regime publisher (nexus_ml) as fallback."""
    try:
        raw = await redis.r.get("nexus:regime:current")
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode()
            return str(json.loads(raw).get("regime", "") or "")
    except Exception:
        pass
    return ""


async def _store_trade_meta(order_id: str, meta: dict[str, Any]) -> None:
    """Persist signal context at order time so close events can be enriched."""
    if not order_id:
        return
    try:
        # Regime at entry is what matters for learning — fall back to the
        # live regime publisher when the order didn't carry one.
        if not meta.get("regime"):
            meta["regime"] = await _current_regime()
        await redis.r.set(
            f"nexus:trade_meta:{order_id}",
            json.dumps(meta, default=str),
            ex=TRADE_META_TTL_SEC,
        )
    except Exception:
        log.exception("executor.trade_meta_store_failed", order_id=order_id)


async def _save_entry_ctx(
    order_id: str,
    symbol: str,
    side: str,
    fill_price: float,
    sl: float | None,
    tp: float | None,
) -> None:
    """Snapshot the live M5 feature vector at order time for outcome-labelled training."""
    if not order_id:
        return
    try:
        feat_raw = await redis.r.get(f"nexus:features:{symbol.upper()}:M5")
        if not feat_raw:
            return
        feat_data = json.loads(feat_raw) if isinstance(feat_raw, (str, bytes)) else {}
        features = feat_data.get("features", feat_data) if isinstance(feat_data, dict) else {}
        ctx = {
            "features": features,
            "symbol": symbol,
            "side": side,
            "entry": fill_price,
            "sl": sl,
            "tp": tp,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await redis.r.set(
            f"nexus:trade:entry_ctx:{order_id}",
            json.dumps(ctx, default=str),
            ex=48 * 3600,
        )
    except Exception:
        log.debug("executor.entry_ctx_save_failed", order_id=order_id)


async def _load_trade_meta(order_id: str) -> dict[str, Any]:
    try:
        raw = await redis.r.get(f"nexus:trade_meta:{order_id}")
        if not raw:
            return {}
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)
    except Exception:
        return {}


def _planned_rr(entry: float, sl: float, tp: float) -> float:
    """Reward:risk planned at entry; 0 when SL/TP were not set."""
    risk = abs(entry - sl)
    if entry <= 0 or sl <= 0 or tp <= 0 or risk <= 0:
        return 0.0
    return round(abs(tp - entry) / risk, 2)


_broker: BrokerAdapter | None = None
_broker_connected = False


# ── Broker Factory ────────────────────────────────────────────────────────────

def _create_broker(broker_type: str) -> BrokerAdapter:
    if broker_type == "mt5":
        return MT5Executor()
    elif broker_type == "ibkr":
        return IBKRExecutor()
    elif broker_type in ("ccxt", "binance", "bybit", "okx"):
        exchange_id = broker_type if broker_type != "ccxt" else "binance"
        return CCXTExecutor(exchange_id=exchange_id)
    else:
        log.warning("executor.unknown_broker", broker=broker_type, fallback="mt5")
        return MT5Executor()


async def _get_broker() -> BrokerAdapter:
    global _broker, _broker_connected
    if _broker is None:
        _broker = _create_broker(DEFAULT_BROKER)
    if not _broker_connected:
        try:
            _broker_connected = await _broker.connect()
            log.info("executor.broker_connected", broker=DEFAULT_BROKER)
        except Exception as exc:
            log.error("executor.broker_connect_failed", error=str(exc))
    return _broker


# ── Request / Response Models ─────────────────────────────────────────────────

class SendOrderRequest(BaseModel):
    symbol: str
    side: str
    order_type: str = "MARKET"
    size: float = 0.01
    price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    slippage: int = 10
    comment: str = "nexus:manual"
    use_smart_routing: bool = True
    bid: float = 0.0
    ask: float = 0.0
    current_spread: float = 0.0
    # Signal context — persisted at order time so close telemetry is complete.
    strategy: str = ""
    conviction: float = 0.0
    session: str = ""
    atr: float = 0.0
    regime: str = ""


class ModifyPositionRequest(BaseModel):
    sl: float | None = None
    tp: float | None = None


class OrderResponseModel(BaseModel):
    order_id: str = ""
    symbol: str = ""
    side: str = ""
    order_type: str = ""
    requested_price: float = 0.0
    fill_price: float = 0.0
    size: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    status: str = "PENDING"
    error: str = ""
    broker: str = ""
    routing: dict[str, Any] = Field(default_factory=dict)


# ── Background Stream Consumer ────────────────────────────────────────────────

async def _execution_stream_consumer() -> None:
    """Consume approved risk decisions and execute orders."""
    await redis.connect()
    log.info("executor.stream_consumer.started")

    async for event in redis.subscribe_stream(
        RedisStream.RISK_DECISION,
        CONSUMER_GROUP,
        CONSUMER_NAME,
    ):
        try:
            decision = getattr(event, "decision", "")
            if decision != "APPROVE":
                log.debug("executor.skipping_rejected", decision=decision)
                continue

            event_data = event.data if isinstance(event.data, dict) else {}
            if event_data.get("skip_execution") or event_data.get("already_executed"):
                log.info(
                    "executor.stream_consumer.skip_already_executed",
                    symbol=event_data.get("symbol", getattr(event, "symbol", "")),
                    order_id=event_data.get("order_id", ""),
                )
                continue
            symbol = event_data.get("symbol", getattr(event, "symbol", ""))
            side = event_data.get("side", event_data.get("direction", "BUY"))
            size = float(getattr(event, "approved_size", 0.01))
            entry = float(event_data.get("entry", event_data.get("price", 0)) or 0)
            sl = float(event_data.get("sl", 0) or 0)
            tp = float(event_data.get("tp", event_data.get("tp1", 0)) or 0)

            if not symbol or size <= 0:
                log.warning(
                    "executor.skipping_incomplete_decision",
                    symbol=symbol,
                    size=size,
                    has_data=bool(event_data),
                )
                continue

            # ── Dedup: skip if this symbol+direction was already executed ─
            if await _is_duplicate_order(symbol, side):
                log.info(
                    "executor.stream_consumer.dedup_skip",
                    symbol=symbol,
                    side=side,
                    reason="order already executed recently via REST or other path",
                )
                continue

            # ── Anti-stacking: never pile onto an already-open direction ──
            if await _stacking_blocked(symbol, side):
                log.info(
                    "executor.stream_consumer.stacking_block",
                    symbol=symbol,
                    side=side,
                    max_per_direction=MAX_PER_DIRECTION,
                )
                continue

            # ── Kill switch check ─────────────────────────────────────
            kill_switch = await redis.cache_get("system:kill_switch")
            if kill_switch:
                log.warning(
                    "executor.kill_switch_active",
                    symbol=symbol,
                    side=side,
                    size=size,
                )
                continue

            strategy = str(event_data.get("strategy", event_data.get("source", "risk")))
            comment = normalize_nexus_comment(
                str(event_data.get("comment", "")),
                strategy=strategy,
            )
            size = await _clamp_lot_size(size, symbol)
            order_req = OrderRequest(
                symbol=symbol,
                side=OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL,
                order_type=OrderType.MARKET,
                size=size,
                price=entry,
                sl=sl,
                tp=tp,
                comment=comment,
            )

            broker = await _get_broker()
            result = await broker.send_order(order_req)

            if result.is_success():
                await _mark_order_executed(symbol, side)
                await _store_trade_meta(result.order_id, {
                    "strategy": _strategy_from_comment(comment) or strategy,
                    "conviction": float(event_data.get("conviction", 0) or 0),
                    "session": str(event_data.get("session", "") or ""),
                    "atr": float(event_data.get("atr", 0) or 0),
                    "regime": str(event_data.get("regime", "") or ""),
                    "entry": result.fill_price,
                    "sl": sl,
                    "tp": tp,
                    "size": result.size,
                    "rr_planned": _planned_rr(result.fill_price, sl, tp),
                    "comment": comment,
                })
                await _save_entry_ctx(result.order_id, symbol, side, result.fill_price, sl, tp)

            order_event = OrderEvent(
                source=SERVICE_NAME,
                correlation_id=event.correlation_id,
                order_id=result.order_id,
                symbol=result.symbol,
                side=result.side,
                order_type=result.order_type,
                price=result.fill_price,
                size=result.size,
                status=result.status.value,
            )
            await redis.publish_event(RedisStream.ORDER_STREAM, order_event)

            if result.is_success():
                pos_event = PositionUpdateEvent(
                    source=SERVICE_NAME,
                    correlation_id=event.correlation_id,
                    position_id=result.order_id,
                    symbol=result.symbol,
                    current_price=result.fill_price,
                    status="open",
                )
                await redis.publish_event(RedisStream.POSITION_UPDATE, pos_event)

        except Exception:
            log.exception("executor.stream_consumer.error")


# ── Position Monitor (detect SL/TP closes by broker) ─────────────────────────

_tracked_positions: dict[str, dict[str, Any]] = {}

POSITION_POLL_SEC = int(os.getenv("NEXUS_POSITION_POLL_SEC", "5"))


async def _position_monitor() -> None:
    """Poll broker positions and detect when a position disappears (SL/TP hit).

    MT5 (and most brokers) close positions server-side when SL/TP is reached.
    The position simply vanishes from ``get_positions()``.  This monitor
    detects the removal and publishes a ``TradeCloseEvent`` so the data
    pipeline can persist the trade to history and the dashboard shows it.
    """
    await asyncio.sleep(8)
    log.info("executor.position_monitor.started", poll_sec=POSITION_POLL_SEC)

    while True:
        try:
            broker = await _get_broker()
            live_positions = await broker.get_positions()
            live_ids = {str(p["id"]) for p in live_positions}

            for pid, tracked in list(_tracked_positions.items()):
                if pid not in live_ids:
                    log.info(
                        "executor.position_monitor.closed_detected",
                        position_id=pid,
                        symbol=tracked.get("symbol", ""),
                    )
                    entry_price = float(tracked.get("entry_price", 0))
                    pnl_usd = float(tracked.get("pnl_usd", 0))
                    side = tracked.get("side", "BUY")
                    symbol = tracked.get("symbol", "")

                    exit_price = float(tracked.get("current_price", 0) or tracked.get("exit_price", 0))
                    if exit_price <= 0:
                        exit_price = await _fetch_last_price(symbol, side)

                    pnl_pips = 0.0
                    if entry_price > 0 and exit_price > 0:
                        diff = exit_price - entry_price
                        if side == "SELL":
                            diff = -diff
                        pnl_pips = round(price_to_pips(symbol, diff), 1)

                    meta = await _load_trade_meta(pid)
                    comment = normalize_nexus_comment(str(tracked.get("comment", "")))
                    close_event = TradeCloseEvent(
                        source=SERVICE_NAME,
                        trade_id=pid,
                        symbol=symbol,
                        side=side,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        pnl_pips=pnl_pips,
                        pnl_usd=pnl_usd,
                        rr_ratio=float(meta.get("rr_planned", 0) or 0),
                        reason="sl_tp_hit",
                        opened_at=str(tracked.get("opened_at", "")),
                        session=str(meta.get("session") or tracked.get("session", "")),
                        conviction=float(
                            meta.get("conviction") or tracked.get("conviction", 0) or 0
                        ),
                        size=float(tracked.get("size", 0)),
                        atr=float(meta.get("atr", 0) or 0),
                        regime=str(meta.get("regime", "") or ""),
                        data={
                            "source": comment,
                            "strategy": str(
                                meta.get("strategy") or _strategy_from_comment(comment)
                            ),
                        },
                    )
                    await redis.publish_event(RedisStream.TRADE_CLOSE, close_event)
                    del _tracked_positions[pid]

            for p in live_positions:
                _tracked_positions[str(p["id"])] = p

            # ── Trailing SL ──────────────────────────────────────────────
            # Move SL to breakeven once profit >= 1.5× SL distance.
            # Trail further once profit >= 2.5× SL distance.
            for p in live_positions:
                try:
                    pid = str(p["id"])
                    side = str(p.get("side", "BUY"))
                    entry_price = float(p.get("entry_price", 0) or 0)
                    current_price = float(p.get("current_price", 0) or 0)
                    current_sl = float(p.get("sl", 0) or 0)

                    if entry_price <= 0 or current_price <= 0 or current_sl <= 0:
                        continue

                    sl_dist = abs(entry_price - current_sl)
                    if sl_dist < 1e-6:
                        continue

                    new_sl: float | None = None

                    if side == "BUY":
                        profit_dist = current_price - entry_price
                        if profit_dist >= sl_dist * 2.5:
                            # Trail: keep SL at current_price minus half the original SL distance
                            trail = current_price - sl_dist * 0.5
                            if trail > current_sl + 1e-6:
                                new_sl = round(trail, 5)
                        elif profit_dist >= sl_dist * 1.5:
                            # Breakeven: move SL just above entry
                            be = entry_price + sl_dist * 0.1
                            if be > current_sl + 1e-6:
                                new_sl = round(be, 5)
                    else:  # SELL
                        profit_dist = entry_price - current_price
                        if profit_dist >= sl_dist * 2.5:
                            trail = current_price + sl_dist * 0.5
                            if trail < current_sl - 1e-6:
                                new_sl = round(trail, 5)
                        elif profit_dist >= sl_dist * 1.5:
                            be = entry_price - sl_dist * 0.1
                            if be < current_sl - 1e-6:
                                new_sl = round(be, 5)

                    if new_sl is not None:
                        result = await broker.modify_order(pid, sl=new_sl)
                        if result.is_success():
                            log.info(
                                "executor.trailing_sl.updated",
                                pid=pid, side=side,
                                old_sl=current_sl, new_sl=new_sl,
                                profit_dist=round(profit_dist, 5),
                            )
                            _tracked_positions[pid]["sl"] = new_sl
                except Exception:
                    log.debug("executor.trailing_sl.error", exc_info=True)

        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("executor.position_monitor.error")

        await asyncio.sleep(POSITION_POLL_SEC)


# ── RL Lifecycle Action Executor ─────────────────────────────────────────────
# Reads RL recommendations from Redis and executes them on the broker.
# Only acts on non-HOLD actions to avoid excessive broker calls.

LIFECYCLE_POLL_SEC = int(os.getenv("NEXUS_LIFECYCLE_POLL_SEC", "30"))
_executed_lifecycle: dict[str, str] = {}  # ticket → last executed action


async def _lifecycle_action_executor() -> None:
    """Poll Redis for RL lifecycle recommendations and execute on broker."""
    await asyncio.sleep(15)  # let position monitor start first
    log.info("executor.lifecycle.started", poll_sec=LIFECYCLE_POLL_SEC)

    while True:
        try:
            # Only run if kill switch is off
            kill_switch = await redis.cache_get("system:kill_switch")
            if kill_switch:
                await asyncio.sleep(LIFECYCLE_POLL_SEC)
                continue

            raw = await redis.r.get("nexus:rl:lifecycle:all")
            if not raw:
                await asyncio.sleep(LIFECYCLE_POLL_SEC)
                continue

            import json as _json
            actions = _json.loads(raw)
            broker = await _get_broker()

            for action_data in actions:
                try:
                    ticket = str(action_data.get("ticket", ""))
                    action = str(action_data.get("action", "HOLD"))
                    confidence = float(action_data.get("confidence", 0.0))

                    # Skip HOLD or low-confidence
                    if action == "HOLD" or confidence < 0.55:
                        continue

                    # Skip if we already acted on this ticket with this action
                    prev = _executed_lifecycle.get(ticket)
                    if prev == action:
                        continue

                    symbol = str(action_data.get("symbol", ""))
                    side = str(action_data.get("side", "BUY"))
                    entry_price = float(action_data.get("entry_price", 0.0))
                    current_price = float(action_data.get("current_price", entry_price))
                    lot_size = float(action_data.get("lot_size", 0.01))

                    if action == "CLOSE_NOW":
                        result = await broker.close_position(ticket)
                        if result.is_success():
                            log.info("executor.lifecycle.close_now",
                                     ticket=ticket, symbol=symbol, confidence=confidence)
                            _executed_lifecycle[ticket] = action
                        else:
                            log.warning("executor.lifecycle.close_now_failed",
                                        ticket=ticket, error=result.error)

                    elif action == "MOVE_SL_BREAKEVEN":
                        # Set SL just at entry price (+10% of SL distance on favorable side)
                        pip_value = 0.01 if "XAU" in symbol else 0.0001
                        sl_buffer = 5 * pip_value  # 5 pip buffer past entry
                        new_sl = (entry_price + sl_buffer) if side == "BUY" else (entry_price - sl_buffer)
                        result = await broker.modify_order(ticket, sl=round(new_sl, 5))
                        if result.is_success():
                            log.info("executor.lifecycle.breakeven",
                                     ticket=ticket, new_sl=new_sl, confidence=confidence)
                            _executed_lifecycle[ticket] = action

                    elif action == "TAKE_PARTIAL_50PCT":
                        partial_size = round(lot_size * 0.5, 2)
                        if partial_size >= 0.01:
                            result = await broker.close_position(ticket, size=partial_size)
                            if result.is_success():
                                log.info("executor.lifecycle.partial_50",
                                         ticket=ticket, partial_lots=partial_size, confidence=confidence)
                                _executed_lifecycle[ticket] = action

                    elif action == "TRAIL_SL_1ATR":
                        # Get ATR from Redis features
                        try:
                            feat_raw = await redis.r.get(f"nexus:features:{symbol}:M5")
                            atr = 5.0
                            if feat_raw:
                                feat_data = _json.loads(feat_raw)
                                feats = feat_data.get("features", feat_data)
                                atr = float(feats.get("atr_14", feats.get("atr", 5.0)))
                            # Trail 1 ATR from current price
                            pip_value = 0.01 if "XAU" in symbol else 0.0001
                            trail_sl = (current_price - atr) if side == "BUY" else (current_price + atr)
                            result = await broker.modify_order(ticket, sl=round(trail_sl, 5))
                            if result.is_success():
                                log.info("executor.lifecycle.trail_1atr",
                                         ticket=ticket, trail_sl=trail_sl, atr=atr, confidence=confidence)
                                _executed_lifecycle[ticket] = action
                        except Exception:
                            log.debug("executor.lifecycle.trail_atr_error", ticket=ticket, exc_info=True)

                except Exception:
                    log.debug("executor.lifecycle.action_error", exc_info=True)

        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("executor.lifecycle.error")

        await asyncio.sleep(LIFECYCLE_POLL_SEC)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis.connect()
    log.info("executor.service.started", port=SERVICE_PORT, broker=DEFAULT_BROKER)
    consumer_task = asyncio.create_task(_execution_stream_consumer())
    monitor_task = asyncio.create_task(_position_monitor())
    lifecycle_task = asyncio.create_task(_lifecycle_action_executor())
    try:
        yield
    finally:
        lifecycle_task.cancel()
        monitor_task.cancel()
        consumer_task.cancel()
        for t in (lifecycle_task, monitor_task, consumer_task):
            try:
                await t
            except asyncio.CancelledError:
                pass
        if _broker:
            await _broker.disconnect()
        await redis.disconnect()
        log.info("executor.service.stopped")


# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Nexus Executor",
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
        "broker": DEFAULT_BROKER,
        "broker_connected": _broker_connected,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/executor/order", response_model=OrderResponseModel)
async def send_order(req: SendOrderRequest):
    try:
        if await _is_duplicate_order(req.symbol, req.side):
            log.info(
                "executor.rest.dedup_skip",
                symbol=req.symbol,
                side=req.side,
            )
            return OrderResponseModel(
                symbol=req.symbol,
                side=req.side.upper(),
                status="DEDUP_SKIPPED",
                error="Duplicate order: same symbol+direction executed recently",
            )

        if await _stacking_blocked(req.symbol, req.side):
            log.info(
                "executor.rest.stacking_block",
                symbol=req.symbol,
                side=req.side,
                max_per_direction=MAX_PER_DIRECTION,
            )
            return OrderResponseModel(
                symbol=req.symbol,
                side=req.side.upper(),
                status="STACKING_BLOCKED",
                error=(
                    f"Open Nexus position already exists for {req.symbol} {req.side.upper()} "
                    f"(max {MAX_PER_DIRECTION} per direction)"
                ),
            )

        order_side = OrderSide.BUY if req.side.upper() == "BUY" else OrderSide.SELL
        order_type = OrderType(req.order_type.upper()) if req.order_type else OrderType.MARKET

        order = OrderRequest(
            symbol=req.symbol,
            side=order_side,
            order_type=order_type,
            size=await _clamp_lot_size(req.size, req.symbol),
            price=req.price,
            sl=req.sl,
            tp=req.tp,
            slippage=req.slippage,
            comment=normalize_nexus_comment(req.comment, strategy="executor"),
        )

        routing_info: dict[str, Any] = {}
        if req.use_smart_routing and req.bid > 0 and req.ask > 0:
            decision = smart_router.route_order(
                order,
                bid=req.bid,
                ask=req.ask,
                current_spread=req.current_spread,
            )
            order.order_type = decision.order_type
            if decision.adjusted:
                order.price = decision.price
            routing_info = {
                "order_type": decision.order_type.value,
                "price": decision.price,
                "reason": decision.reason,
                "liquidity_ok": decision.liquidity_ok,
                "adjusted": decision.adjusted,
            }

        broker = await _get_broker()
        result = await broker.send_order(order)

        if result.is_success():
            await _mark_order_executed(req.symbol, req.side)
            await _store_trade_meta(result.order_id, {
                "strategy": req.strategy or _strategy_from_comment(order.comment),
                "conviction": req.conviction,
                "session": req.session,
                "atr": req.atr,
                "regime": req.regime,
                "entry": result.fill_price,
                "sl": req.sl,
                "tp": req.tp,
                "size": result.size,
                "rr_planned": _planned_rr(result.fill_price, req.sl, req.tp),
                "comment": order.comment,
            })
            await _save_entry_ctx(result.order_id, req.symbol, req.side, result.fill_price, req.sl, req.tp)
            order_event = OrderEvent(
                source=SERVICE_NAME,
                order_id=result.order_id,
                symbol=result.symbol,
                side=result.side,
                order_type=result.order_type,
                price=result.fill_price,
                size=result.size,
                status=result.status.value,
            )
            await redis.publish_event(RedisStream.ORDER_STREAM, order_event)

        return OrderResponseModel(
            order_id=result.order_id,
            symbol=result.symbol,
            side=result.side,
            order_type=result.order_type,
            requested_price=result.requested_price,
            fill_price=result.fill_price,
            size=result.size,
            sl=result.sl,
            tp=result.tp,
            status=result.status.value,
            error=result.error,
            broker=result.broker,
            routing=routing_info,
        )
    except Exception as exc:
        log.exception("executor.send_order.error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/api/executor/position/{position_id}")
async def close_position(position_id: str, size: float | None = None):
    try:
        broker = await _get_broker()
        result = await broker.close_position(position_id, size=size)

        if result.is_success():
            raw = result.raw_response or {}
            entry_price = float(raw.get("entry_price", result.requested_price) or 0)
            pnl_usd = float(raw.get("pnl_usd", 0) or 0)
            position_side = raw.get("position_side", result.side)
            opened_at = raw.get("opened_at", "")

            sym_upper = result.symbol.upper()
            if "JPY" in sym_upper:
                pip_mult = 100
            elif "XAU" in sym_upper or "XAG" in sym_upper:
                pip_mult = 10
            elif "BTC" in sym_upper or "ETH" in sym_upper:
                pip_mult = 1
            else:
                pip_mult = 10_000
            pnl_pips = 0.0
            if entry_price > 0 and result.fill_price > 0:
                diff = result.fill_price - entry_price
                if position_side == "SELL":
                    diff = -diff
                pnl_pips = round(diff * pip_mult, 1)

            meta = await _load_trade_meta(str(raw.get("ticket", position_id)))
            close_event = TradeCloseEvent(
                source=SERVICE_NAME,
                trade_id=result.order_id,
                symbol=result.symbol,
                side=position_side,
                entry_price=entry_price,
                exit_price=result.fill_price,
                pnl_pips=pnl_pips,
                pnl_usd=pnl_usd,
                rr_ratio=float(meta.get("rr_planned", 0) or 0),
                reason="manual_close",
                opened_at=opened_at,
                session=str(meta.get("session", "") or ""),
                conviction=float(meta.get("conviction", 0) or 0),
                size=result.size,
                atr=float(meta.get("atr", 0) or 0),
                regime=str(meta.get("regime", "") or ""),
                data={
                    "source": str(meta.get("comment", "") or ""),
                    "strategy": str(meta.get("strategy", "") or ""),
                },
            )
            await redis.publish_event(RedisStream.TRADE_CLOSE, close_event)

        return {
            "order_id": result.order_id,
            "status": result.status.value,
            "fill_price": result.fill_price,
            "error": result.error,
        }
    except Exception as exc:
        log.exception("executor.close_position.error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.put("/api/executor/position/{position_id}")
async def modify_position(position_id: str, req: ModifyPositionRequest):
    try:
        broker = await _get_broker()
        result = await broker.modify_order(position_id, sl=req.sl, tp=req.tp)
        return {
            "order_id": result.order_id,
            "status": result.status.value,
            "sl": result.sl,
            "tp": result.tp,
            "error": result.error,
        }
    except Exception as exc:
        log.exception("executor.modify_position.error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/executor/positions")
async def get_positions(nexus_only: bool = False):
    try:
        broker = await _get_broker()
        positions = await broker.get_positions()
        nexus_pos, external_pos = partition_positions(positions)
        account = await broker.get_account_info()
        visible = nexus_pos if nexus_only else positions
        return {
            "positions": visible,
            "nexus_positions": nexus_pos,
            "external_positions": external_pos,
            "count": len(visible),
            "nexus_count": len(nexus_pos),
            "external_count": len(external_pos),
            "total_count": len(positions),
            "account": {
                "balance": account.balance,
                "equity": account.equity,
                "margin": account.margin,
                "free_margin": account.free_margin,
                "currency": account.currency,
                "broker": account.broker,
            },
        }
    except Exception as exc:
        log.exception("executor.get_positions.error")
        raise HTTPException(status_code=500, detail=str(exc))
