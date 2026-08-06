"""
StrategyRegistry — Redis-backed CRUD for the Strategy Lab.

Each strategy is stored as JSON under ``nexus:strategy:<id>`` and the full
list is mirrored in a sorted set ``nexus:strategy:index`` (scored by
``profit_factor`` so the best strategies float to the top).

Rationale: keeping the registry in Redis (instead of DuckDB) lets the
dashboard read it without depending on the DuckDB writer (nexus-data) being
healthy, and avoids cross-service file locking on Windows.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

import structlog
from pydantic import BaseModel, Field

from shared.redis_client import RedisClient

logger = structlog.get_logger(__name__)

# ── Redis keys ──────────────────────────────────────────────────────────────
INDEX_KEY = "strategy:index"            # sorted set (score = profit_factor)
ITEM_KEY = "strategy:item:{id}"          # JSON blob per strategy
LIVE_DECISION_KEY = "strategy:decision:{symbol}"  # last verdict per symbol
RESEARCH_LOG_KEY = "strategy:research:log"        # capped list of recent runs

ALL_STATUSES = ("active", "backtested", "investigating", "rejected", "paused")


# ── Promotion rules ──────────────────────────────────────────────────────────

@dataclass
class PromotionRules:
    """Thresholds a candidate must beat to be promoted to ``active``."""

    min_profit_factor: float = 1.4
    min_win_rate: float = 0.52
    min_sharpe: float = 0.8
    max_drawdown_pct: float = 18.0
    min_trades: int = 30
    min_net_pnl_pct: float = 5.0

    @classmethod
    def from_env(cls) -> "PromotionRules":
        def f(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, str(default)))
            except ValueError:
                return default

        def i(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)))
            except ValueError:
                return default

        return cls(
            min_profit_factor=f("STRATEGY_MIN_PROFIT_FACTOR", 1.4),
            min_win_rate=f("STRATEGY_MIN_WIN_RATE", 0.52),
            min_sharpe=f("STRATEGY_MIN_SHARPE", 0.8),
            max_drawdown_pct=f("STRATEGY_MAX_DD_PCT", 18.0),
            min_trades=i("STRATEGY_MIN_TRADES", 30),
            min_net_pnl_pct=f("STRATEGY_MIN_NET_PNL_PCT", 5.0),
        )

    def evaluate(self, metrics: dict[str, Any]) -> tuple[bool, list[str]]:
        """Return (passed, reasons_list)."""
        reasons: list[str] = []
        pf = float(metrics.get("profit_factor", 0) or 0)
        wr = float(metrics.get("win_rate", 0) or 0)
        sh = float(metrics.get("sharpe", 0) or 0)
        dd = abs(float(metrics.get("max_dd_pct", 0) or 0))
        trades = int(metrics.get("total_trades", 0) or 0)
        net = float(metrics.get("net_pnl_pct", 0) or 0)

        if pf < self.min_profit_factor:
            reasons.append(f"profit_factor {pf:.2f} < {self.min_profit_factor:.2f}")
        if wr < self.min_win_rate:
            reasons.append(f"win_rate {wr:.2f} < {self.min_win_rate:.2f}")
        if sh < self.min_sharpe:
            reasons.append(f"sharpe {sh:.2f} < {self.min_sharpe:.2f}")
        if dd > self.max_drawdown_pct:
            reasons.append(f"max_dd {dd:.1f}% > {self.max_drawdown_pct:.1f}%")
        if trades < self.min_trades:
            reasons.append(f"trades {trades} < {self.min_trades}")
        if net < self.min_net_pnl_pct:
            reasons.append(f"net_pnl {net:.1f}% < {self.min_net_pnl_pct:.1f}%")

        return (len(reasons) == 0, reasons)


# ── Strategy record ──────────────────────────────────────────────────────────


class StrategyRecordModel(BaseModel):
    """Pydantic validation layer for strategy persistence."""

    id: str
    name: str
    short_name: str = ""
    type: str = ""
    description: str = ""
    status: str = "investigating"
    pairs: list[str] = Field(default_factory=list)
    timeframes: list[str] = Field(default_factory=lambda: ["M5"])
    agents: list[str] = Field(default_factory=list)
    entry_logic: str = ""
    exit_logic: str = ""
    mql5_signal_code: str = ""
    color: str = "cyan"
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe: float = 0.0
    max_dd_pct: float = 0.0
    total_trades: int = 0
    avg_rr: float = 0.0
    net_pnl_pct: float = 0.0
    recent_results: list[bool] = Field(default_factory=list)
    promotion_reasons: list[str] = Field(default_factory=list)
    proposed_by: str = "A12"
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class StrategyRecord:
    """Persisted shape of a strategy investigated by the system."""

    id: str
    name: str
    short_name: str
    type: str
    description: str
    status: str = "investigating"     # active|backtested|investigating|rejected|paused
    pairs: list[str] = field(default_factory=list)
    timeframes: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    entry_logic: str = ""
    exit_logic: str = ""
    mql5_signal_code: str = ""
    color: str = "cyan"
    # Performance snapshot (from last evaluation)
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe: float = 0.0
    max_dd_pct: float = 0.0
    total_trades: int = 0
    avg_rr: float = 0.0
    net_pnl_pct: float = 0.0
    recent_results: list[bool] = field(default_factory=list)
    promotion_reasons: list[str] = field(default_factory=list)
    proposed_by: str = "A12"
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StrategyRecord":
        clean = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**clean)


# ── Registry ────────────────────────────────────────────────────────────────

class StrategyRegistry:
    """Async CRUD for strategies persisted in Redis."""

    def __init__(self, redis: RedisClient) -> None:
        self.redis = redis
        self.rules = PromotionRules.from_env()

    # ─── Helpers ────────────────────────────────────────────────────────────

    def _key(self, strategy_id: str) -> str:
        return ITEM_KEY.format(id=strategy_id)

    def _full(self, key: str) -> str:
        from shared.constants import REDIS_KEY_PREFIX
        return f"{REDIS_KEY_PREFIX}{key}"

    def new_id(self, prefix: str = "S") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:8]}"

    # ─── Read ───────────────────────────────────────────────────────────────

    async def get(self, strategy_id: str) -> StrategyRecord | None:
        raw = await self.redis.cache_get(self._key(strategy_id))
        if not raw or not isinstance(raw, dict):
            return None
        try:
            return StrategyRecord.from_dict(raw)
        except Exception:
            await logger.awarning("registry.bad_record", id=strategy_id)
            return None

    async def list(self, *, status: str | None = None) -> list[StrategyRecord]:
        index = self._full(INDEX_KEY)
        try:
            ids = await self.redis.r.zrevrange(index, 0, -1)
        except Exception:
            await logger.awarning("registry.index_unavailable")
            return []

        out: list[StrategyRecord] = []
        for sid in ids:
            rec = await self.get(sid)
            if rec is None:
                continue
            if status and rec.status != status:
                continue
            out.append(rec)
        return out

    async def list_active(self) -> list[StrategyRecord]:
        return await self.list(status="active")

    # ─── Write ──────────────────────────────────────────────────────────────

    async def save(self, record: StrategyRecord, *, ttl_sec: int = 60 * 60 * 24 * 30) -> None:
        now = time.time()
        if not record.created_at:
            record.created_at = now
        record.updated_at = now

        validated = StrategyRecordModel.model_validate(record.to_dict()).model_dump()
        await self.redis.cache_set(self._key(record.id), validated, ttl_sec=ttl_sec)
        # Index by profit factor so list() returns best first
        try:
            await self.redis.r.zadd(self._full(INDEX_KEY), {record.id: float(record.profit_factor)})
        except Exception:
            await logger.awarning("registry.index_zadd_failed", id=record.id)

    async def delete(self, strategy_id: str) -> None:
        await self.redis.cache_delete(self._key(strategy_id))
        try:
            await self.redis.r.zrem(self._full(INDEX_KEY), strategy_id)
        except Exception:
            pass

    async def set_status(self, strategy_id: str, status: str, *, reasons: list[str] | None = None) -> StrategyRecord | None:
        if status not in ALL_STATUSES:
            raise ValueError(f"invalid status {status!r}")
        rec = await self.get(strategy_id)
        if rec is None:
            return None
        rec.status = status
        if reasons is not None:
            rec.promotion_reasons = reasons
        await self.save(rec)
        return rec

    # ─── Decision pub/sub for live dashboard ────────────────────────────────

    async def publish_live_decision(self, symbol: str, payload: dict[str, Any]) -> None:
        """Cache + publish the latest A7+A8 decision for a symbol."""
        await self.redis.cache_set(LIVE_DECISION_KEY.format(symbol=symbol), payload, ttl_sec=600)
        try:
            await self.redis.publish_channel("strategy:decision", {"symbol": symbol, **payload})
        except Exception:
            await logger.awarning("registry.decision_publish_failed", symbol=symbol)

    async def get_live_decision(self, symbol: str) -> dict[str, Any] | None:
        raw = await self.redis.cache_get(LIVE_DECISION_KEY.format(symbol=symbol))
        return raw if isinstance(raw, dict) else None

    # ─── Research log (recent runs) ─────────────────────────────────────────

    async def log_research_run(self, summary: dict[str, Any]) -> None:
        try:
            key = self._full(RESEARCH_LOG_KEY)
            await self.redis.r.lpush(key, json.dumps(summary, default=str))
            await self.redis.r.ltrim(key, 0, 49)
        except Exception:
            await logger.awarning("registry.research_log_failed")

    async def get_research_log(self, limit: int = 25) -> list[dict[str, Any]]:
        try:
            raw = await self.redis.r.lrange(self._full(RESEARCH_LOG_KEY), 0, limit - 1)
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for item in raw:
            try:
                out.append(json.loads(item))
            except Exception:
                continue
        return out

    # ─── Promotion ──────────────────────────────────────────────────────────

    async def evaluate_and_promote(self, record: StrategyRecord) -> StrategyRecord:
        passed, reasons = self.rules.evaluate(record.to_dict())
        record.promotion_reasons = reasons
        if passed:
            record.status = "active"
        elif record.total_trades < self.rules.min_trades:
            record.status = "investigating"
        else:
            record.status = "rejected"
        await self.save(record)
        return record


# ── Bulk seed used the first time the registry is empty ──────────────────────

def default_seed_strategies() -> Iterable[StrategyRecord]:
    """A small pre-populated set so the dashboard never looks empty.

    These are real, well-known patterns; they will be re-evaluated by the
    backtester as soon as nexus-data has OHLCV and overwritten with live
    metrics.
    """
    base = time.time()
    return [
        StrategyRecord(
            id="seed-sr-bounce",
            name="S/R Bounce + Volume Confluence",
            short_name="S/R Bounce",
            type="Price Action",
            description="Mean-reverting entry at strong S/R levels confirmed by volume spike (>=1.5x avg) and AI conviction.",
            pairs=["XAUUSD", "EURUSD", "GBPUSD"],
            timeframes=["M15", "H1"],
            agents=["A1", "A4", "A7", "A8"],
            entry_logic="Price wicks key S/R + volume >= 1.5x avg + agent conviction >= 70",
            exit_logic="TP1 1:1 (50%), TP2 1.5:1 (30%), TP3 trailing (20%); 15min time exit",
            mql5_signal_code=_MQL5_SR_BOUNCE,
            color="green",
            status="investigating",
            proposed_by="seed",
            created_at=base,
        ),
        StrategyRecord(
            id="seed-ema-stack",
            name="Triple EMA Stack + AI Filter",
            short_name="EMA Stack",
            type="Trend Following",
            description="Triple EMA alignment (9/21/50) on H1 with AI conviction gate.",
            pairs=["EURUSD", "GBPUSD", "XAUUSD"],
            timeframes=["H1", "H4"],
            agents=["A1", "A5", "A6", "A7", "A8"],
            entry_logic="EMA9>EMA21>EMA50 + fresh cross + conviction>=60",
            exit_logic="TP1 1:1, TP2 1.5:1, TP3 trailing, 15min time exit",
            mql5_signal_code=_MQL5_EMA_STACK,
            color="cyan",
            status="investigating",
            proposed_by="seed",
            created_at=base,
        ),
        StrategyRecord(
            id="seed-session-breakout",
            name="Session Open Momentum Breakout",
            short_name="Session Open",
            type="Session-Based",
            description="Captures momentum during first 30 minutes of London/NY open with news blackout.",
            pairs=["EURUSD", "GBPUSD", "XAUUSD"],
            timeframes=["M5", "M15"],
            agents=["A1", "A2", "A11", "A8"],
            entry_logic="Session open candle > 0.5x prior range + no news blackout",
            exit_logic="TP1 1:1, hard time exit at 30min",
            mql5_signal_code=_MQL5_SESSION_BREAKOUT,
            color="gold",
            status="investigating",
            proposed_by="seed",
            created_at=base,
        ),
    ]


# ─── Reusable signal stubs ───────────────────────────────────────────────────

_MQL5_SR_BOUNCE = """// S/R Bounce + Volume Confluence signal
int GetDirection() {
   double atr = iATR(_Symbol, PERIOD_CURRENT, 14, 0);
   double close = iClose(_Symbol, PERIOD_CURRENT, 1);
   double vol = (double)iVolume(_Symbol, PERIOD_CURRENT, 1);
   double volAvg = 0; for(int i=2;i<=21;i++) volAvg += (double)iVolume(_Symbol, PERIOD_CURRENT, i); volAvg /= 20.0;
   double resistance = iHigh(_Symbol, PERIOD_H1, iHighest(_Symbol, PERIOD_H1, MODE_HIGH, 20, 1));
   double support    = iLow(_Symbol,  PERIOD_H1, iLowest (_Symbol, PERIOD_H1, MODE_LOW,  20, 1));
   bool volC = vol >= volAvg * 1.5;
   if (close > support - atr*0.3 && volC) return  1;
   if (close < resistance + atr*0.3 && volC) return -1;
   return 0;
}
int GetConviction() { return 70; }
"""

_MQL5_EMA_STACK = """// Triple EMA Stack signal
int GetDirection() {
   double e9  = iMA(_Symbol, PERIOD_H1, 9,  0, MODE_EMA, PRICE_CLOSE, 0);
   double e21 = iMA(_Symbol, PERIOD_H1, 21, 0, MODE_EMA, PRICE_CLOSE, 0);
   double e50 = iMA(_Symbol, PERIOD_H1, 50, 0, MODE_EMA, PRICE_CLOSE, 0);
   if (e9 > e21 && e21 > e50) return  1;
   if (e9 < e21 && e21 < e50) return -1;
   return 0;
}
int GetConviction() { return 65; }
"""

_MQL5_SESSION_BREAKOUT = """// Session Open Momentum Breakout signal
int GetDirection() {
   MqlDateTime dt; TimeToStruct(TimeGMT(), dt);
   int mins = dt.hour * 60 + dt.min;
   bool inLondon = mins >= 420 && mins < 450;
   bool inNY     = mins >= 720 && mins < 750;
   if (!inLondon && !inNY) return 0;
   double op = iOpen(_Symbol, PERIOD_M15, 0);
   double hi20 = iHigh(_Symbol, PERIOD_M15, iHighest(_Symbol, PERIOD_M15, MODE_HIGH, 20, 1));
   double lo20 = iLow(_Symbol,  PERIOD_M15, iLowest (_Symbol, PERIOD_M15, MODE_LOW,  20, 1));
   double range = hi20 - lo20;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if (ask > op + range * 0.5) return  1;
   if (bid < op - range * 0.5) return -1;
   return 0;
}
int GetConviction() { return 60; }
"""
