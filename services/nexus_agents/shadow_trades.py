"""
Shadow trades — records what would have happened had a rejected/downgraded
signal actually been taken.

Every time the Arbitrator produces a NO_TRADE or WATCH verdict with a
directional lean and conviction >= _SHADOW_MIN_CONVICTION, this logs a
hypothetical entry. A background resolver checks back 60 minutes later,
compares the hypothetical entry to the price at that point, and records
whether it would have won or lost.

This is the only way to know whether a gate (the ergodic circuit breaker,
the conflict-lock, an institutional threshold bump) is filtering genuine
noise or quietly costing real edge — without it, every tightening of a gate
is a guess.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

import structlog

from shared.redis_client import RedisClient

logger = structlog.get_logger(__name__)

_SHADOW_MIN_CONVICTION = int(os.getenv("NEXUS_SHADOW_MIN_CONVICTION", "60"))
_RESOLVE_AFTER_MIN = int(os.getenv("NEXUS_SHADOW_RESOLVE_MIN", "60"))
_PENDING_KEY = "nexus:shadow_trades:pending"
_RESOLVED_KEY = "nexus:shadow_trades:resolved"
_RESOLVED_MAX_LEN = 2000

# XAUUSD-style pip conversion; good enough for the symbols this system trades.
_PIP_MULT = {"XAU": 10, "XAG": 10, "JPY": 100}


def _pip_multiplier(symbol: str) -> float:
    for tag, mult in _PIP_MULT.items():
        if tag in symbol.upper():
            return mult
    return 10_000


async def record_shadow_trade(
    redis: RedisClient,
    *,
    symbol: str,
    side: str,
    entry_price: float,
    conviction: int,
    reason: str,
    session: str,
    verdict: str,
) -> None:
    """Log a hypothetical trade for a rejected/downgraded signal.

    No-ops below the minimum conviction — we only care about signals that
    were strong enough to plausibly have been real trades.
    """
    if side not in ("BUY", "SELL") or conviction < _SHADOW_MIN_CONVICTION or entry_price <= 0:
        return

    now = datetime.now(timezone.utc)
    record = {
        "id": uuid.uuid4().hex[:12],
        "symbol": symbol,
        "side": side,
        "entry_price": entry_price,
        "conviction": conviction,
        "reason": reason[:200],
        "session": session,
        "verdict": verdict,  # NO_TRADE or WATCH — what actually happened
        "opened_at": now.isoformat(),
        "resolve_at": now.timestamp() + _RESOLVE_AFTER_MIN * 60,
    }
    try:
        await redis.r.rpush(_PENDING_KEY, json.dumps(record))
        await redis.r.ltrim(_PENDING_KEY, -5000, -1)
    except Exception:
        logger.debug("shadow_trades.record_failed", exc_info=True)


async def resolve_due_shadow_trades(redis: RedisClient, get_price) -> int:
    """Resolve any pending shadow trades whose 60-minute window has elapsed.

    ``get_price`` is an async callable ``(symbol) -> float | None`` that
    returns the current mid/close price. Returns the number resolved.
    """
    try:
        raw_list = await redis.r.lrange(_PENDING_KEY, 0, -1)
    except Exception:
        logger.debug("shadow_trades.resolve_read_failed", exc_info=True)
        return 0

    if not raw_list:
        return 0

    now_ts = datetime.now(timezone.utc).timestamp()
    still_pending: list[str] = []
    resolved_count = 0

    for raw in raw_list:
        try:
            rec = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        if now_ts < rec.get("resolve_at", 0):
            still_pending.append(raw)
            continue

        try:
            current_price = await get_price(rec["symbol"])
        except Exception:
            current_price = None

        if not current_price or current_price <= 0:
            # Price feed hiccup — keep it pending for the next sweep rather
            # than losing the record.
            still_pending.append(raw)
            continue

        entry = float(rec["entry_price"])
        diff = current_price - entry
        if rec["side"] == "SELL":
            diff = -diff
        pips = round(diff * _pip_multiplier(rec["symbol"]), 1)

        rec["exit_price"] = current_price
        rec["pnl_pips"] = pips
        rec["would_have_won"] = pips > 0
        rec["resolved_at"] = datetime.now(timezone.utc).isoformat()

        try:
            await redis.r.rpush(_RESOLVED_KEY, json.dumps(rec))
            await redis.r.ltrim(_RESOLVED_KEY, -_RESOLVED_MAX_LEN, -1)
            resolved_count += 1
        except Exception:
            logger.debug("shadow_trades.resolve_write_failed", exc_info=True)
            still_pending.append(raw)

    if resolved_count:
        try:
            await redis.r.delete(_PENDING_KEY)
            if still_pending:
                await redis.r.rpush(_PENDING_KEY, *still_pending)
        except Exception:
            logger.debug("shadow_trades.resolve_trim_failed", exc_info=True)

    return resolved_count


async def get_shadow_stats(redis: RedisClient, limit: int = 500) -> dict:
    """Aggregate stats over resolved shadow trades — the payoff of this module.

    Compares what the gates rejected against what actually would have
    happened, split by verdict type (NO_TRADE vs WATCH) so a WR/PF here can
    be read against the system's real WR/PF.
    """
    try:
        raw_list = await redis.r.lrange(_RESOLVED_KEY, -limit, -1)
    except Exception:
        return {"total": 0, "error": "redis unavailable"}

    records = []
    for raw in raw_list:
        try:
            records.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue

    if not records:
        return {"total": 0, "win_rate": None, "avg_pips": None, "by_verdict": {}}

    def _summarize(items: list[dict]) -> dict:
        if not items:
            return {"n": 0, "win_rate": None, "avg_pips": None}
        wins = sum(1 for r in items if r.get("would_have_won"))
        avg_pips = sum(float(r.get("pnl_pips", 0)) for r in items) / len(items)
        return {
            "n": len(items),
            "win_rate": round(wins / len(items), 3),
            "avg_pips": round(avg_pips, 2),
        }

    by_verdict = {
        "NO_TRADE": _summarize([r for r in records if r.get("verdict") == "NO_TRADE"]),
        "WATCH": _summarize([r for r in records if r.get("verdict") == "WATCH"]),
    }

    return {
        "total": len(records),
        **_summarize(records),
        "by_verdict": by_verdict,
    }
