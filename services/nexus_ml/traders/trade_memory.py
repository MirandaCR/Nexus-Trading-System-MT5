"""
Trade Memory — feedback loop that learns from winning and losing trades.

Consumes TRADE_CLOSE events, maintains a rolling window of recent trades,
computes per-condition performance metrics, and exposes ``get_trade_bias()``
which returns confidence adjustments that downstream consensus / risk
callers can apply before opening new positions.

Redis keys written (all with configurable TTL):
    nexus:trade_memory:trades          — JSON list of recent trade records
    nexus:trade_memory:metrics         — aggregated metric breakdown
    nexus:trade_memory:bias            — latest bias snapshot
    nexus:trade_memory:cooling_off     — present when in cooling-off period
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
MEMORY_WINDOW = int(os.getenv("NEXUS_TRADE_MEMORY_WINDOW", "200"))
METRICS_TTL = int(os.getenv("NEXUS_TRADE_MEMORY_TTL", "3600"))
COOLING_OFF_TRADES = int(os.getenv("NEXUS_COOLING_OFF_STREAK", "3"))
COOLING_OFF_CYCLES = int(os.getenv("NEXUS_COOLING_OFF_CYCLES", "1"))

_KEY_TRADES = "nexus:trade_memory:trades"
_KEY_METRICS = "nexus:trade_memory:metrics"
_KEY_BIAS = "nexus:trade_memory:bias"
_KEY_COOLING = "nexus:trade_memory:cooling_off"
_KEY_THRESHOLDS = "nexus:trade_memory:thresholds"

# ── Session classification by UTC hour ────────────────────────────────────────

def _hour_to_session(utc_hour: int) -> str:
    from shared.sessions import detect_session
    return detect_session(utc_hour).value


# ── Regime tag normalizer ─────────────────────────────────────────────────────

_VALID_REGIMES = {"TRENDING_UP", "TRENDING_DOWN", "RANGING", "HIGH_VOLATILITY", "UNKNOWN"}
_REGIME_MAP = {
    "": "UNKNOWN",
    "trade": "UNKNOWN",
    "automl": "UNKNOWN",
    "TRENDING": "TRENDING_UP",
    "trending": "TRENDING_UP",
    "trending_up": "TRENDING_UP",
    "trending_down": "TRENDING_DOWN",
    "ranging": "RANGING",
    "high_volatility": "HIGH_VOLATILITY",
}


def _normalize_regime(raw: str) -> str:
    if not raw:
        return "UNKNOWN"
    upper = raw.upper()
    if upper in _VALID_REGIMES:
        return upper
    return _REGIME_MAP.get(raw, _REGIME_MAP.get(upper, "UNKNOWN"))


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    trade_id: str = ""
    symbol: str = ""
    side: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl_pips: float = 0.0
    pnl_usd: float = 0.0
    session: str = ""
    regime: str = ""
    source: str = ""           # "autonomous_ml" | "genai_agents"
    atr: float = 0.0
    conviction: float = 0.0
    closed_at: str = ""
    is_win: bool = False
    _hour_bucket: str = ""     # "H00"-"H23" derived from closed_at

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TradeRecord:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class TradeBias:
    """Confidence adjustments returned by ``get_trade_bias()``."""
    confidence_mult: float = 1.0
    skip_cycle: bool = False
    no_trade: bool = False
    reasons: list[str] = field(default_factory=list)

    # Convenience helpers
    @property
    def effective_multiplier(self) -> float:
        if self.no_trade or self.skip_cycle:
            return 0.0
        return max(0.0, self.confidence_mult)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Core class ────────────────────────────────────────────────────────────────

class TradeMemory:
    """Rolling trade performance tracker stored in Redis."""

    _RECONCILE_INTERVAL_SEC = 600  # cap DuckDB reconciliation to once/10min —
    # _load() runs on every agent cycle and must stay cheap after startup.

    def __init__(self, redis_conn: aioredis.Redis | None = None) -> None:
        self._redis = redis_conn
        self._trades: list[TradeRecord] = []
        self._metrics: dict[str, Any] = {}
        self._last_reconcile: float = 0.0

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(REDIS_URL, decode_responses=True, max_connections=5)
        return self._redis

    # ── Trade ingestion ───────────────────────────────────────────────────

    async def record_trade(
        self,
        *,
        trade_id: str = "",
        symbol: str = "",
        side: str = "",
        entry_price: float = 0.0,
        exit_price: float = 0.0,
        pnl_pips: float = 0.0,
        pnl_usd: float = 0.0,
        session: str = "",
        regime: str = "",
        source: str = "",
        atr: float = 0.0,
        conviction: float = 0.0,
    ) -> None:
        """Record a completed trade and recompute metrics."""
        if not session:
            session = _hour_to_session(datetime.now(timezone.utc).hour)

        regime = _normalize_regime(regime)

        now = datetime.now(timezone.utc)
        rec = TradeRecord(
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl_pips=pnl_pips,
            pnl_usd=pnl_usd,
            session=session,
            regime=regime,
            source=source,
            atr=atr,
            conviction=conviction,
            closed_at=now.isoformat(),
            is_win=pnl_pips > 0,
            _hour_bucket=f"H{now.hour:02d}",
        )

        self._trades.append(rec)
        window = MEMORY_WINDOW
        try:
            r = await self._get_redis()
            raw = await r.get("nexus:runtime:settings")
            if raw:
                window = int(json.loads(raw).get("trade_memory_window", MEMORY_WINDOW))
        except Exception:
            pass
        if len(self._trades) > window:
            self._trades = self._trades[-window:]

        self._recompute_metrics()
        await self._persist()
        # Recompute dynamic thresholds after every trade so all services see fresh values.
        try:
            await self.compute_dynamic_thresholds()
        except Exception:
            logger.debug("trade_memory: threshold recompute failed", exc_info=True)

    # ── Metric computation ────────────────────────────────────────────────

    def _recompute_metrics(self) -> None:
        if not self._trades:
            self._metrics = {}
            return

        m: dict[str, Any] = {
            "total_trades": len(self._trades),
            "total_wins": sum(1 for t in self._trades if t.is_win),
            "total_losses": sum(1 for t in self._trades if not t.is_win),
            "overall_win_rate": 0.0,
            "by_session": {},
            "by_regime": {},
            "by_direction": {},
            "by_source": {},
            "by_hour": {},
            "by_vol_bucket": {},
            "recent_streak": self._recent_streak(),
            "avg_pnl_pips": 0.0,
        }
        total = m["total_trades"]
        m["overall_win_rate"] = m["total_wins"] / total if total else 0.0
        m["avg_pnl_pips"] = sum(t.pnl_pips for t in self._trades) / total if total else 0.0

        for bucket_key, attr in [
            ("by_session", "session"),
            ("by_regime", "regime"),
            ("by_direction", "side"),
            ("by_source", "source"),
            ("by_hour", "_hour_bucket"),
        ]:
            groups: dict[str, list[TradeRecord]] = defaultdict(list)
            for t in self._trades:
                val = getattr(t, attr, "") or "UNKNOWN"
                groups[val.upper()].append(t)

            for label, trades in groups.items():
                wins = sum(1 for t in trades if t.is_win)
                n = len(trades)
                m[bucket_key][label] = {
                    "trades": n,
                    "wins": wins,
                    "losses": n - wins,
                    "win_rate": wins / n if n else 0.0,
                    "avg_pnl": sum(t.pnl_pips for t in trades) / n if n else 0.0,
                }

        # Volatility buckets based on ATR percentile within the window
        atrs = [t.atr for t in self._trades if t.atr > 0]
        if atrs:
            atrs_sorted = sorted(atrs)
            median = atrs_sorted[len(atrs_sorted) // 2]
            for t in self._trades:
                bucket = "HIGH_VOL" if t.atr > median else "LOW_VOL"
                m["by_vol_bucket"].setdefault(bucket, {"trades": 0, "wins": 0, "avg_pnl": 0.0, "pnl_sum": 0.0})
                m["by_vol_bucket"][bucket]["trades"] += 1
                if t.is_win:
                    m["by_vol_bucket"][bucket]["wins"] += 1
                m["by_vol_bucket"][bucket]["pnl_sum"] += t.pnl_pips

            for bucket in m["by_vol_bucket"]:
                n = m["by_vol_bucket"][bucket]["trades"]
                if n:
                    m["by_vol_bucket"][bucket]["win_rate"] = m["by_vol_bucket"][bucket]["wins"] / n
                    m["by_vol_bucket"][bucket]["avg_pnl"] = m["by_vol_bucket"][bucket]["pnl_sum"] / n

        self._metrics = m

    def _recent_streak(self) -> dict[str, Any]:
        """Count consecutive wins or losses from the most recent trades."""
        if not self._trades:
            return {"type": "none", "count": 0}
        last_result = self._trades[-1].is_win
        count = 0
        for t in reversed(self._trades):
            if t.is_win == last_result:
                count += 1
            else:
                break
        return {"type": "win" if last_result else "loss", "count": count}

    # ── Bias computation ──────────────────────────────────────────────────

    async def get_trade_bias(
        self,
        *,
        session: str = "",
        direction: str = "",
        regime: str = "",
        source: str = "",
    ) -> TradeBias:
        """Return confidence adjustments based on recent trade memory.

        Called by the consensus module and GenAI agents before opening a new
        position.  The bias combines several heuristics:
          - Session win rate too low → reduce confidence
          - Direction win rate too low → reduce confidence
          - Losing streak → cooling off (skip cycle)
          - Regime win rate too low → NO_TRADE
        """
        await self._load()
        bias = TradeBias()

        if not self._metrics or self._metrics.get("total_trades", 0) < 5:
            return bias

        # Cooling-off check
        r = await self._get_redis()
        cooling = await r.exists(_KEY_COOLING)
        if cooling:
            bias.skip_cycle = True
            bias.reasons.append(
                f"Cooling off after {COOLING_OFF_TRADES} consecutive losses"
            )
            return bias

        streak = self._metrics.get("recent_streak", {})
        if streak.get("type") == "loss" and streak.get("count", 0) >= COOLING_OFF_TRADES:
            bias.skip_cycle = True
            cool_sec = 300
            try:
                raw = await r.get("nexus:runtime:settings")
                if raw:
                    cool_sec = int(json.loads(raw).get("cooling_off_duration_sec", 300))
            except Exception:
                pass
            bias.reasons.append(
                f"Losing streak of {streak['count']} — entering cooling-off ({cool_sec}s)"
            )
            await r.set(_KEY_COOLING, "1", ex=cool_sec)
            return bias

        # Session win-rate adjustment
        if session:
            sess_data = self._metrics.get("by_session", {}).get(session.upper(), {})
            sess_wr = sess_data.get("win_rate", 0.5)
            sess_n = sess_data.get("trades", 0)
            if sess_n >= 5 and sess_wr < 0.30:
                bias.confidence_mult *= 0.80
                bias.reasons.append(
                    f"Session {session} win rate {sess_wr:.0%} < 30% — confidence -20%"
                )

        # Direction win-rate adjustment
        if direction:
            dir_data = self._metrics.get("by_direction", {}).get(direction.upper(), {})
            dir_wr = dir_data.get("win_rate", 0.5)
            dir_n = dir_data.get("trades", 0)
            if dir_n >= 5 and dir_wr < 0.35:
                bias.confidence_mult *= 0.85
                bias.reasons.append(
                    f"Direction {direction} win rate {dir_wr:.0%} < 35% — confidence -15%"
                )

        # Regime win-rate → NO_TRADE
        if regime:
            reg_data = self._metrics.get("by_regime", {}).get(regime.upper(), {})
            reg_wr = reg_data.get("win_rate", 0.5)
            reg_n = reg_data.get("trades", 0)
            if reg_n >= 5 and reg_wr < 0.25:
                bias.no_trade = True
                bias.reasons.append(
                    f"Regime {regime} win rate {reg_wr:.0%} < 25% — NO_TRADE"
                )

        # Source win-rate adjustment
        if source:
            src_data = self._metrics.get("by_source", {}).get(source.upper(), {})
            src_wr = src_data.get("win_rate", 0.5)
            src_n = src_data.get("trades", 0)
            if src_n >= 5 and src_wr < 0.30:
                bias.confidence_mult *= 0.80
                bias.reasons.append(
                    f"Source {source} win rate {src_wr:.0%} < 30% — confidence -20%"
                )

        return bias

    async def get_memory_snapshot(self) -> dict[str, Any]:
        """Return the full metrics dict (for GenAI agents to read)."""
        await self._load()
        return {
            "metrics": self._metrics,
            "trade_count": len(self._trades),
            "last_trade": self._trades[-1].to_dict() if self._trades else {},
            "recent_streak": self._metrics.get("recent_streak", {}),
            "overall_win_rate": self._metrics.get("overall_win_rate", 0.0),
        }

    async def compute_dynamic_thresholds(self) -> dict[str, Any]:
        """Derive all trading thresholds from real WR data — nothing hardcoded.

        Returns a dict that is also persisted to Redis so every service can
        read current thresholds without hitting this class directly.
        """
        await self._load()
        m = self._metrics
        total = m.get("total_trades", 0)
        overall_wr = m.get("overall_win_rate", 0.5)

        # Signal quality score threshold — adaptive to system performance.
        # Good system (WR>55%): relax to 55. Bad system (WR<40%): tighten to 75.
        if total >= 20:
            if overall_wr >= 0.55:
                quality_threshold = 55
            elif overall_wr >= 0.45:
                quality_threshold = 60
            elif overall_wr >= 0.35:
                quality_threshold = 68
            else:
                quality_threshold = 75
        else:
            quality_threshold = 62  # conservative until we have real data

        # Per-regime minimum WR to enable trading in that regime.
        regime_min_wr: dict[str, float] = {}
        for regime, stats in m.get("by_regime", {}).items():
            n = stats.get("trades", 0)
            wr = stats.get("win_rate", 0.5)
            # Only use if we have enough trades; otherwise allow (no data = no block).
            regime_min_wr[regime] = wr if n >= 8 else 0.0

        # Best and worst hours (UTC) for trading.
        hour_wr: dict[str, float] = {}
        for hour, stats in m.get("by_hour", {}).items():
            n = stats.get("trades", 0)
            if n >= 3:
                hour_wr[hour] = stats.get("win_rate", 0.5)

        good_hours = [h for h, wr in hour_wr.items() if wr >= 0.50]
        bad_hours = [h for h, wr in hour_wr.items() if wr < 0.30]

        # Confidence multiplier: pulls down system-wide min conviction when WR is bad.
        if total >= 20:
            if overall_wr >= 0.55:
                conviction_min = 0.52
            elif overall_wr >= 0.45:
                conviction_min = 0.58
            else:
                conviction_min = 0.65
        else:
            conviction_min = 0.60

        # Average RR from winning trades in each regime.
        regime_avg_rr: dict[str, float] = {}
        for regime, stats in m.get("by_regime", {}).items():
            n = stats.get("trades", 0)
            avg_pnl = stats.get("avg_pnl", 0.0)
            if n >= 5 and avg_pnl > 0:
                regime_avg_rr[regime] = round(avg_pnl, 1)

        thresholds = {
            "quality_score_threshold": quality_threshold,
            "conviction_min": round(conviction_min, 3),
            "regime_min_wr": regime_min_wr,
            "regime_avg_rr": regime_avg_rr,
            "good_hours": good_hours,
            "bad_hours": bad_hours,
            "hour_wr": hour_wr,
            "overall_wr": round(overall_wr, 4),
            "total_trades": total,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

        # Persist so other services can read without importing this module.
        try:
            r = await self._get_redis()
            await r.set(_KEY_THRESHOLDS, json.dumps(thresholds, default=str), ex=METRICS_TTL)
        except Exception:
            logger.debug("trade_memory: failed to persist thresholds", exc_info=True)

        return thresholds

    async def get_dynamic_thresholds(self) -> dict[str, Any]:
        """Read thresholds from Redis (fast path) or compute on cache miss."""
        try:
            r = await self._get_redis()
            raw = await r.get(_KEY_THRESHOLDS)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return await self.compute_dynamic_thresholds()

    # ── Persistence ───────────────────────────────────────────────────────

    async def _persist(self) -> None:
        """Write current state to Redis."""
        try:
            r = await self._get_redis()
            trade_dicts = [t.to_dict() for t in self._trades[-MEMORY_WINDOW:]]
            pipe = r.pipeline()
            pipe.set(_KEY_TRADES, json.dumps(trade_dicts), ex=2_592_000)   # 30 days
            pipe.set(_KEY_METRICS, json.dumps(self._metrics, default=str), ex=86_400)
            await pipe.execute()
        except Exception:
            logger.error("trade_memory: failed to persist", exc_info=True)

    async def _load(self) -> None:
        """Load state from Redis, then reconcile against the full DuckDB trade
        history so a truncated in-memory/Redis window never permanently caps
        what the adaptive thresholds learn from.
        """
        if not self._trades:
            try:
                r = await self._get_redis()
                raw_trades = await r.get(_KEY_TRADES)
                if raw_trades:
                    data = json.loads(raw_trades)
                    self._trades = [TradeRecord.from_dict(d) for d in data]
                raw_metrics = await r.get(_KEY_METRICS)
                if raw_metrics:
                    self._metrics = json.loads(raw_metrics)
                elif self._trades:
                    self._recompute_metrics()
            except Exception:
                logger.debug("trade_memory: failed to load from Redis", exc_info=True)

        # Reconcile with nexus_api's full trade journal (DuckDB-backed)
        # periodically — not just when the cache is empty. A Redis snapshot
        # that predates a service restart, or was truncated by an older
        # MEMORY_WINDOW, would otherwise stay stuck below the real trade
        # count forever. Throttled since _load() runs on every agent cycle.
        now = time.monotonic()
        if now - self._last_reconcile < self._RECONCILE_INTERVAL_SEC:
            return
        self._last_reconcile = now

        before = len(self._trades)
        await self._backfill_from_api()
        if len(self._trades) != before:
            self._recompute_metrics()
            try:
                await self._persist()
                logger.info(
                    "trade_memory: reconciled to %d trades (was %d), WR=%.0f%%",
                    len(self._trades), before,
                    self._metrics.get("overall_win_rate", 0) * 100,
                )
            except Exception:
                logger.debug("trade_memory: persist after reconcile failed", exc_info=True)

    async def _backfill_from_api(self) -> None:
        """Merge in any nexus_api (DuckDB) trades not already held in memory.

        De-duplicates by trade_id so this is safe to call on every startup,
        not just when the in-memory list is empty.
        """
        import os
        import httpx
        from shared.asset_specs import get_asset_spec as _gspec

        api_url = os.getenv("NEXUS_API_URL", "http://localhost:9000")
        known_ids = {t.trade_id for t in self._trades if t.trade_id}
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    f"{api_url}/api/trades",
                    params={"page": 1, "page_size": 200},
                )
                if resp.status_code != 200:
                    return
                data = resp.json()
                trades = data.get("trades", []) if isinstance(data, dict) else data
                if not trades:
                    return

                added = 0
                for t in trades:
                    try:
                        trade_id = str(t.get("id", ""))
                        if trade_id and trade_id in known_ids:
                            continue
                        sym = t.get("symbol", "")
                        try:
                            canonical = _gspec(sym).symbol
                        except Exception:
                            canonical = sym.upper()
                        pnl_usd = float(t.get("pnl_usd", 0) or 0)
                        closed_at = str(t.get("closed_at", "") or "")
                        record = TradeRecord(
                            trade_id=trade_id,
                            symbol=canonical,
                            side=str(t.get("side", "")),
                            entry_price=float(t.get("entry_price", 0) or 0),
                            exit_price=float(t.get("exit_price", 0) or 0),
                            pnl_pips=float(t.get("pnl_pips", 0) or 0),
                            pnl_usd=pnl_usd,
                            session=str(t.get("session", "") or ""),
                            regime=str(t.get("strategy", "") or ""),
                            source="historical",
                            conviction=float(t.get("conviction", 0) or 0),
                            closed_at=closed_at,
                            is_win=pnl_usd > 0,
                        )
                        self._trades.append(record)
                        if trade_id:
                            known_ids.add(trade_id)
                        added += 1
                    except Exception:
                        continue

                if added:
                    self._trades.sort(key=lambda t: t.closed_at)
                    window = MEMORY_WINDOW
                    try:
                        raw = await (await self._get_redis()).get("nexus:runtime:settings")
                        if raw:
                            window = int(json.loads(raw).get("trade_memory_window", MEMORY_WINDOW))
                    except Exception:
                        pass
                    if len(self._trades) > window:
                        self._trades = self._trades[-window:]
                    logger.info("trade_memory: merged %d new historical trades from nexus_api", added)
        except Exception as exc:
            logger.debug("trade_memory: backfill from api failed: %s", exc)


# ── Background stream listener ────────────────────────────────────────────────

async def _trade_close_listener(memory: TradeMemory) -> None:
    """Subscribe to TRADE_CLOSE stream and feed closed trades into memory."""
    from shared.constants import RedisStream

    r = await memory._get_redis()

    group = "trade-memory-cg"
    consumer = "trade-memory-1"
    stream = str(RedisStream.TRADE_CLOSE)

    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception:
        pass

    logger.info("trade_memory: stream listener started on %s", stream)

    while True:
        try:
            entries = await r.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={stream: ">"},
                count=10,
                block=3000,
            )
            if not entries:
                continue
            for _stream_name, messages in entries:
                for msg_id, fields in messages:
                    try:
                        comment = str(
                            fields.get("source", "")
                            or fields.get("data.source", "")
                            or fields.get("comment", "")
                        )
                        source = fields.get("source", "") or fields.get("data.source", "")
                        if not source and "genai" in comment.lower():
                            source = "genai_agents"
                        elif not source and "autonomous" in comment.lower():
                            source = "autonomous_ml"
                        elif not source and comment.startswith("nexus:"):
                            source = "genai_agents"

                        trade_id = fields.get("trade_id", "")
                        symbol = fields.get("symbol", "")
                        pnl_pips = float(fields.get("pnl_pips", 0) or 0)

                        await memory.record_trade(
                            trade_id=trade_id,
                            symbol=symbol,
                            side=fields.get("side", ""),
                            entry_price=float(fields.get("entry_price", 0) or 0),
                            exit_price=float(fields.get("exit_price", 0) or 0),
                            pnl_pips=pnl_pips,
                            pnl_usd=float(fields.get("pnl_usd", 0) or 0),
                            session=fields.get("session", ""),
                            regime=fields.get("regime", ""),
                            source=source,
                            atr=float(fields.get("atr", 0) or 0),
                            conviction=float(fields.get("conviction", 0) or 0),
                        )
                        logger.info(
                            "trade_memory: recorded trade %s %s pnl=%.1f",
                            symbol, fields.get("side"), pnl_pips,
                        )

                        # Push outcome-labelled record so ML/DL trainers can
                        # learn from actual TP/SL results, not just price direction.
                        if trade_id and symbol:
                            try:
                                entry_raw = await r.get(f"nexus:trade:entry_ctx:{trade_id}")
                                if entry_raw:
                                    entry_ctx = json.loads(entry_raw)
                                    features = entry_ctx.get("features", {})
                                    if features:
                                        label = 1 if pnl_pips > 0 else 0
                                        outcome_record = json.dumps({
                                            "features": features,
                                            "label": label,
                                            "ohlcv": {
                                                "open": float(features.get("open", 0)),
                                                "high": float(features.get("high", 0)),
                                                "low": float(features.get("low", 0)),
                                                "close": float(features.get("close", 0)),
                                                "volume": float(features.get("volume", 0)),
                                            },
                                            "timestamp": entry_ctx.get("timestamp", ""),
                                            "outcome_weight": 5.0,
                                        })
                                        # Normalize to canonical internal symbol (GOLD → XAUUSD)
                                        try:
                                            from shared.asset_specs import get_asset_spec as _gspec
                                            canonical_sym = _gspec(symbol).symbol
                                        except Exception:
                                            canonical_sym = symbol.upper()
                                        outcomes_key = f"nexus:features:outcomes:{canonical_sym}"
                                        await r.rpush(outcomes_key, outcome_record)
                                        await r.ltrim(outcomes_key, -2000, -1)
                                        logger.info(
                                            "trade_memory: outcome label=%d saved for %s→%s %s",
                                            label, symbol, canonical_sym, trade_id,
                                        )
                            except Exception:
                                logger.debug("trade_memory: outcome push failed", exc_info=True)
                    except Exception:
                        logger.error("trade_memory: failed to process trade close", exc_info=True)
                    finally:
                        await r.xack(stream, group, msg_id)
        except asyncio.CancelledError:
            logger.info("trade_memory: stream listener cancelled")
            return
        except Exception:
            logger.error("trade_memory: stream listener error", exc_info=True)
            await asyncio.sleep(2)


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: TradeMemory | None = None
_listener_task: asyncio.Task | None = None


def get_trade_memory() -> TradeMemory:
    """Return the module-level singleton."""
    global _instance
    if _instance is None:
        _instance = TradeMemory()
    return _instance


async def start_trade_memory() -> TradeMemory:
    """Initialise and start the background listener.  Idempotent."""
    global _listener_task
    mem = get_trade_memory()
    await mem._load()
    if _listener_task is None or _listener_task.done():
        _listener_task = asyncio.create_task(_trade_close_listener(mem))
    return mem


async def stop_trade_memory() -> None:
    global _listener_task
    if _listener_task and not _listener_task.done():
        _listener_task.cancel()
        try:
            await _listener_task
        except asyncio.CancelledError:
            pass
    _listener_task = None
