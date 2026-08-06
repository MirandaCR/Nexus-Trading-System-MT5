"""
Backtest Router — parameter-filter simulation on historical closed trades.

Fetches all closed trades from nexus-data, applies the requested config filters
(symbol, date range, min conviction, sessions, strategies), and computes:
  - Core metrics: total trades, WR, PF, net PnL, avg trade, max drawdown, Sharpe
  - Equity curve (cumulative PnL per trade)
  - Breakdown by session, strategy, hour-of-day, direction

POST /api/backtest      — run a backtest, returns full result
GET  /api/backtest/summary — last cached result (quick re-read)

This is a "walk-forward filter" backtest: it does NOT re-simulate entries/exits,
it only asks "what would the performance have been if we ONLY took trades that
pass the given parameter set?"
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from ..http_client import new_client
import redis.asyncio as aioredis
from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backtest", tags=["Backtest"])

_DATA_URL  = os.getenv("NEXUS_DATA_URL", "http://localhost:8001")
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
_CACHE_KEY = "nexus:backtest:last_result"
_CACHE_TTL = 3600

_redis: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(_REDIS_URL, decode_responses=True, max_connections=3)
    return _redis


# ── Config schema ─────────────────────────────────────────────────────────────

class BacktestConfig(BaseModel):
    symbol: str = "XAUUSD"
    date_from: str | None = None          # ISO date "2026-01-01"
    date_to: str | None = None            # ISO date "2026-12-31"
    min_conviction: float = Field(0.0, ge=0.0, le=1.0)
    sessions: list[str] = Field(default_factory=list)   # [] = all
    strategies: list[str] = Field(default_factory=list) # [] = all
    directions: list[str] = Field(default_factory=list) # [] = both
    min_pips: float = 0.0
    initial_equity: float = 5000.0


# ── Trade fetcher ─────────────────────────────────────────────────────────────

async def _fetch_trades(symbol: str) -> list[dict]:
    """Fetch all trades for the given symbol from Redis trade_memory.

    Reads nexus:trade_memory:trades directly — same source as the TradeJournal —
    so the backtest sees the full historical window (up to 100 trades) rather
    than only the trades that nexus_data's DuckDB consumer has persisted.
    Falls back to the DuckDB endpoint if Redis is unavailable.
    """
    try:
        r = await _get_redis()
        raw = await r.get("nexus:trade_memory:trades")
        if raw:
            all_trades: list[dict] = json.loads(raw)
            sym = symbol.upper()
            return [t for t in all_trades if str(t.get("symbol", "")).upper() == sym]
    except Exception as exc:
        logger.warning("backtest.fetch_trades redis failed: %s — falling back to DuckDB", exc)

    try:
        async with new_client(timeout=15.0) as client:
            resp = await client.get(
                f"{_DATA_URL}/api/data/trades",
                params={"symbol": symbol, "page_size": 2000, "page": 1},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("trades", data) if isinstance(data, dict) else data
    except Exception as exc:
        logger.warning("backtest.fetch_trades DuckDB fallback failed: %s", exc)
        return []


# ── Filtering ─────────────────────────────────────────────────────────────────

def _get(trade: dict, *keys: str, default=None):
    for k in keys:
        if k in trade:
            return trade[k]
    return default


def _apply_filters(trades: list[dict], cfg: BacktestConfig) -> list[dict]:
    filtered = []
    for t in trades:
        # Date range
        ts = _get(t, "closed_at", "close_time", "open_time", "timestamp", default="")
        if ts:
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if cfg.date_from:
                    from_dt = datetime.fromisoformat(cfg.date_from).replace(tzinfo=timezone.utc)
                    if dt < from_dt:
                        continue
                if cfg.date_to:
                    to_dt = datetime.fromisoformat(cfg.date_to).replace(tzinfo=timezone.utc)
                    if dt > to_dt:
                        continue
            except Exception:
                pass

        # Min conviction
        if cfg.min_conviction > 0:
            conv = _get(t, "conviction", "signal_conviction", default=1.0)
            try:
                if float(conv or 1.0) < cfg.min_conviction:
                    continue
            except (TypeError, ValueError):
                pass

        # Sessions
        if cfg.sessions:
            sess = _get(t, "session", "trading_session", default="")
            if sess.upper() not in [s.upper() for s in cfg.sessions]:
                continue

        # Strategies
        if cfg.strategies:
            strat = _get(t, "strategy", "signal_source", "source", default="")
            if strat not in cfg.strategies:
                continue

        # Directions
        if cfg.directions:
            direction = _get(t, "direction", "side", "type", default="")
            if direction.upper() not in [d.upper() for d in cfg.directions]:
                continue

        # Min pips
        if cfg.min_pips > 0:
            pips = abs(float(_get(t, "pips", "pips_result", default=0) or 0))
            if pips < cfg.min_pips:
                continue

        filtered.append(t)
    return filtered


# ── Metrics computation ───────────────────────────────────────────────────────

def _compute_metrics(trades: list[dict], initial_equity: float) -> dict:
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "net_pnl": 0.0, "avg_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "max_drawdown": 0.0, "max_dd_pct": 0.0, "sharpe": 0.0,
            "wins": 0, "losses": 0,
        }

    pnls = [float(_get(t, "pnl", "pnl_usd", "profit", default=0) or 0) for t in trades]
    wins  = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    gross_profit = sum(wins) if wins else 0.0
    gross_loss   = abs(sum(losses)) if losses else 0.0

    # Equity curve for max drawdown
    equity = initial_equity
    peak = equity
    max_dd = 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    # Sharpe (annualized, assuming ~252 trading days, ~10 trades/day max)
    mean_pnl = sum(pnls) / len(pnls)
    std_pnl  = math.sqrt(sum((p - mean_pnl) ** 2 for p in pnls) / max(len(pnls), 1))
    sharpe   = (mean_pnl / std_pnl * math.sqrt(252)) if std_pnl > 0 else 0.0

    return {
        "total_trades":  len(trades),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      round(len(wins) / len(trades), 4),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else 999.0,
        "net_pnl":       round(sum(pnls), 2),
        "avg_pnl":       round(mean_pnl, 2),
        "avg_win":       round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss":      round(sum(losses) / len(losses), 2) if losses else 0.0,
        "max_drawdown":  round(-max_dd, 2),
        "max_dd_pct":    round(-max_dd / peak * 100, 2) if peak > 0 else 0.0,
        "sharpe":        round(sharpe, 3),
    }


def _equity_curve(trades: list[dict], initial_equity: float) -> list[dict]:
    curve = []
    equity = initial_equity
    for i, t in enumerate(trades):
        pnl = float(_get(t, "pnl", "pnl_usd", "profit", default=0) or 0)
        equity += pnl
        ts = _get(t, "closed_at", "close_time", "open_time", "timestamp", default="")
        curve.append({
            "n":      i + 1,
            "ts":     str(ts)[:10],
            "pnl":    round(pnl, 2),
            "equity": round(equity, 2),
        })
    return curve


def _breakdown(trades: list[dict], key_fn, label: str) -> dict[str, dict]:
    groups: dict[str, list[float]] = {}
    for t in trades:
        k = str(key_fn(t) or "unknown").upper()
        pnl = float(_get(t, "pnl", "pnl_usd", "profit", default=0) or 0)
        groups.setdefault(k, []).append(pnl)
    result = {}
    for k, pnls in groups.items():
        wins = [p for p in pnls if p > 0]
        result[k] = {
            "trades": len(pnls),
            "win_rate": round(len(wins) / len(pnls), 3),
            "net_pnl": round(sum(pnls), 2),
            "avg_pnl": round(sum(pnls) / len(pnls), 2),
        }
    return result


def _hour_breakdown(trades: list[dict]) -> dict[str, dict]:
    groups: dict[str, list[float]] = {}
    for t in trades:
        ts = _get(t, "closed_at", "close_time", "open_time", "timestamp", default="")
        try:
            hour = str(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).hour)
        except Exception:
            hour = "?"
        pnl = float(_get(t, "pnl", "pnl_usd", "profit", default=0) or 0)
        groups.setdefault(hour, []).append(pnl)
    result = {}
    for h, pnls in sorted(groups.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 99):
        wins = [p for p in pnls if p > 0]
        result[h] = {
            "trades": len(pnls),
            "win_rate": round(len(wins) / len(pnls), 3),
            "net_pnl": round(sum(pnls), 2),
        }
    return result


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("")
async def run_backtest(cfg: BacktestConfig) -> dict[str, Any]:
    """Run a filter-based backtest on historical closed trades."""
    raw_trades = await _fetch_trades(cfg.symbol)
    filtered   = _apply_filters(raw_trades, cfg)

    # Sort by close_time ascending for correct equity curve ordering
    def _sort_key(t: dict) -> str:
        return str(_get(t, "close_time", "open_time", "timestamp", default="") or "")
    filtered.sort(key=_sort_key)

    metrics = _compute_metrics(filtered, cfg.initial_equity)
    curve   = _equity_curve(filtered, cfg.initial_equity)

    result: dict[str, Any] = {
        "config": cfg.model_dump(),
        "raw_trades_fetched": len(raw_trades),
        "trades_after_filter": len(filtered),
        "metrics": metrics,
        "equity_curve": curve,
        "by_session":  _breakdown(filtered, lambda t: _get(t, "session", "trading_session"), "session"),
        "by_strategy": _breakdown(filtered, lambda t: _get(t, "strategy", "signal_source", "source"), "strategy"),
        "by_direction": _breakdown(filtered, lambda t: _get(t, "direction", "side"), "direction"),
        "by_hour":     _hour_breakdown(filtered),
        "run_at":      datetime.now(timezone.utc).isoformat(),
    }

    # Cache to Redis for the /summary endpoint
    try:
        r = await _get_redis()
        await r.set(_CACHE_KEY, json.dumps(result), ex=_CACHE_TTL)
    except Exception:
        pass

    return result


@router.get("/summary")
async def last_backtest() -> dict[str, Any]:
    """Return the last cached backtest result, or empty if none."""
    try:
        r = await _get_redis()
        raw = await r.get(_CACHE_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return {"metrics": None, "run_at": None, "message": "No backtest run yet"}


@router.get("/strategies")
async def available_strategies() -> dict[str, Any]:
    """List unique strategy names from recent trades (for the filter dropdown)."""
    try:
        async with new_client(timeout=10.0) as client:
            resp = await client.get(f"{_DATA_URL}/api/data/trades", params={"page_size": 1000})
            resp.raise_for_status()
            data = resp.json()
            trades = data.get("trades", data) if isinstance(data, dict) else data
            strategies = sorted({
                str(_get(t, "strategy", "signal_source", default="unknown") or "unknown")
                for t in trades
            })
            return {"strategies": strategies}
    except Exception:
        return {"strategies": ["nexus_ml", "nexus_agents", "manual"]}
