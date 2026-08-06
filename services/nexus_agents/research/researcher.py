"""
StrategyResearcher — regime-adaptive strategy research engine.

Pipeline per cycle:

1. Gather real OHLCV candles + multi-timeframe context + current regime.
2. Ask the LLM to design strategies **based on what the candles show**,
   not generic indicator combos.
3. Backtest each candidate (in parallel via ``asyncio.gather``).
4. Apply :class:`PromotionRules` to decide ``active`` / ``investigating``
   / ``rejected``.
5. Persist via :class:`StrategyRegistry`.

Regime change detection:
  When the volatility regime changes (e.g. medium → high), all strategies
  are re-evaluated and new candidates designed for the new regime are
  proposed automatically.

The active strategy's entry/exit logic is published to Redis so the
Arbitrator (A7) can factor it into its verdict.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

import structlog

from shared.redis_client import RedisClient
from shared import runtime_settings

from .evaluator import StrategyEvaluator
from .registry import (
    StrategyRecord,
    StrategyRegistry,
    default_seed_strategies,
)

logger = structlog.get_logger(__name__)

# Redis key where the best active strategy is published for the pipeline
ACTIVE_STRATEGY_KEY = "strategy:active:{symbol}"

# Which strategy *types* make sense in each market regime. Used to (a) bias
# candidate generation toward the current regime and (b) decide whether an
# existing strategy is appropriate to trade right now.
_REGIME_TYPES: dict[str, tuple[str, ...]] = {
    "TRENDING": ("trend following", "breakout", "momentum"),
    "RANGING": ("mean reversion", "price action", "session-based"),
    "BREAKOUT": ("breakout", "session-based", "trend following"),
    "UNKNOWN": ("trend following", "mean reversion", "breakout"),
}


def _regime_key(regime: str) -> str:
    r = (regime or "UNKNOWN").upper()
    return r if r in _REGIME_TYPES else "UNKNOWN"


def _strategy_fits_regime(rec: "StrategyRecord", regime: str) -> bool:
    """True if a strategy's type is appropriate for the current regime."""
    prefs = _REGIME_TYPES[_regime_key(regime)]
    ttype = (rec.type or "").lower()
    if any(p in ttype or ttype in p for p in prefs):
        return True
    # Also honour an explicit regime tag baked into the description.
    desc = (rec.description or "").lower()
    return _regime_key(regime).lower() in desc

# ── Prompts ──────────────────────────────────────────────────────────────────

_RESEARCH_PROMPT = """You are the Strategy Researcher of a multi-agent trading system trading {symbol}.
You receive REAL market data below — actual candles, indicators, and multi-timeframe context.

=== RECENT OHLCV CANDLES (last {candle_count}, {timeframe}) ===
{candles}

=== CURRENT INDICATORS ({timeframe}) ===
{indicators}

=== MULTI-TIMEFRAME CONTEXT ===
{mtf_context}

=== MARKET REGIME ===
Volatility regime: {vol_regime}
Trend direction: {trend_direction}
Trend strength: {trend_strength}
Session: {session}
ATR (current): {atr}
ATR (normalised): {atr_norm}

=== PERFORMANCE OF CURRENT STRATEGIES ===
{existing}

=== RECENT TRADE RESULTS ===
{recent_results}

TASK: Analyse the candles above. Look at price action patterns, support/resistance
levels, indicator readings, and the multi-timeframe alignment. Then design {n} NEW
strategies that are specifically optimised for CURRENT market conditions.

Key constraints:
- The regime is "{vol_regime}" — design for THIS volatility level.
- HTF trend is {trend_direction} — strategies should respect or explicitly fade it.
- Session is {session} — consider liquidity and typical ranges.
- Look at the ACTUAL candle data: are there recent sweeps, consolidations, breakouts?
- Be SPECIFIC with indicator thresholds (e.g. "RSI crosses 40 from below" not just "RSI signal").

Respond with STRICT JSON:
{{
  "market_read": "1-2 sentence description of what you see in the candles",
  "strategies": [
    {{
      "name": "string",
      "short_name": "<= 24 chars",
      "type": "Trend Following | Mean Reversion | Breakout | Price Action | Session-Based",
      "description": "<= 280 chars",
      "pairs": ["{symbol}"],
      "timeframes": {timeframes_json},
      "agents": ["A1", "A7", "A8"],
      "entry_logic": "<= 250 chars, SPECIFIC thresholds based on what you see",
      "exit_logic": "<= 250 chars, mention TP structure and trailing",
      "regime_fit": "{vol_regime}",
      "htf_alignment": "with_trend | counter_trend | neutral"
    }}
  ]
}}

Output JSON ONLY. No prose, no markdown fence.
"""


# ── Researcher ──────────────────────────────────────────────────────────────


class StrategyResearcher:
    """Regime-adaptive, candle-driven strategy researcher."""

    def __init__(
        self,
        redis: RedisClient,
        *,
        evaluator: StrategyEvaluator | None = None,
        proposer_agent_id: str = "A12",
    ) -> None:
        self.redis = redis
        self.registry = StrategyRegistry(redis)
        self.evaluator = evaluator or StrategyEvaluator()
        self.proposer_agent_id = proposer_agent_id
        self._last_regime: str = ""
        self._last_trend: str = ""

    # ── Public surface ──────────────────────────────────────────────────────

    async def ensure_seed(self) -> int:
        """Populate the registry with seed strategies if empty."""
        existing = await self.registry.list()
        if existing:
            return 0
        count = 0
        for rec in default_seed_strategies():
            evaluated = await self._evaluate_and_save(rec)
            if evaluated is not None:
                count += 1
        await logger.ainfo("research.seeded", count=count)
        return count

    async def run_cycle(self, *, n: int = 2) -> dict[str, Any]:
        """Propose ``n`` candidates, evaluate them in parallel, return summary."""
        started = time.time()
        existing = await self.registry.list()
        existing_names = [r.name for r in existing]

        # Detect regime change — triggers full re-evaluation
        market_ctx = await self._fetch_full_context()
        current_regime = market_ctx.get("vol_regime", "medium")
        current_trend = market_ctx.get("trend_direction", "neutral")
        regime_changed = (
            self._last_regime
            and current_regime != self._last_regime
        )
        if regime_changed:
            await logger.ainfo(
                "research.regime_change",
                old=self._last_regime,
                new=current_regime,
            )
        self._last_regime = current_regime
        self._last_trend = current_trend

        proposals = await self._generate_candidates(
            n=n, existing_names=existing_names, context=market_ctx,
        )
        accepted, rejected = await self._evaluate_proposals(proposals, market_ctx)

        # Publish the best active strategy for the pipeline to use
        await self._publish_active_strategy(market_ctx.get("symbol", "XAUUSD"))

        summary = {
            "ts": started,
            "duration_ms": int((time.time() - started) * 1000),
            "proposed": len(proposals),
            "accepted": accepted,
            "rejected": rejected,
            "regime": current_regime,
            "trend": current_trend,
            "regime_changed": regime_changed,
        }
        await self.registry.log_research_run(summary)
        await self._announce(summary)
        return summary

    async def _evaluate_proposals(
        self,
        proposals: list[dict[str, Any]],
        market_ctx: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Backtest + persist a batch of proposals in PARALLEL.

        Returns (accepted, rejected) where accepted = promoted to ``active``.
        """
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        eval_tasks = []
        for spec in proposals:
            try:
                rec = self._spec_to_record(spec, market_ctx)
            except Exception as exc:
                await logger.awarning("research.bad_spec", error=str(exc))
                continue
            eval_tasks.append(self._evaluate_and_save(rec))

        if not eval_tasks:
            return accepted, rejected

        results = await asyncio.gather(*eval_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception) or result is None:
                continue
            saved: StrategyRecord = result
            payload = {
                "id": saved.id,
                "name": saved.name,
                "type": saved.type,
                "status": saved.status,
                "profit_factor": saved.profit_factor,
                "win_rate": saved.win_rate,
                "sharpe": saved.sharpe,
                "max_dd_pct": saved.max_dd_pct,
                "total_trades": saved.total_trades,
                "regime_fit": saved.description[:50],
                "promotion_reasons": saved.promotion_reasons,
            }
            (accepted if saved.status == "active" else rejected).append(payload)
        return accepted, rejected

    async def run_adaptive_cycle(self) -> dict[str, Any]:
        """Regime-aware, search-until-found research cycle.

        Goal: minimise dead time by always having at least one *tradeable*
        strategy that fits the CURRENT market regime. Logic:

          1. Detect the live regime (TRENDING / RANGING / BREAKOUT).
          2. If an ``active`` strategy already fits the regime → light touch
             (one exploratory batch to keep the lab fresh, no hard search).
          3. Otherwise → SEARCH MODE: design N regime-appropriate candidates
             per round and keep searching (up to ``research_max_search_rounds``)
             until one is promoted to active. Also tries to IMPROVE existing
             strategies that fit the regime but aren't active yet.
        """
        started = time.time()
        settings = await runtime_settings.get_settings(self.redis)
        n = int(settings.get("research_min_candidates", 4))
        max_rounds = int(settings.get("research_max_search_rounds", 3))

        market_ctx = await self._fetch_full_context()
        regime = self._effective_regime(market_ctx)
        self._last_regime = market_ctx.get("vol_regime", self._last_regime)

        existing = await self.registry.list()
        fitting_active = [
            r for r in existing
            if r.status == "active" and _strategy_fits_regime(r, regime)
        ]

        # Try to improve existing strategies that fit the regime but aren't live.
        improved = await self._improve_fitting(existing, regime, market_ctx)

        need_search = not fitting_active and not improved
        rounds = max_rounds if need_search else 1

        total_proposed = 0
        accepted_all: list[dict[str, Any]] = []
        rejected_all: list[dict[str, Any]] = []
        rounds_used = 0

        for _ in range(rounds):
            rounds_used += 1
            existing_names = [r.name for r in await self.registry.list()]
            proposals = await self._generate_candidates(
                n=n, existing_names=existing_names, context=market_ctx, regime=regime,
            )
            total_proposed += len(proposals)
            accepted, rejected = await self._evaluate_proposals(proposals, market_ctx)
            accepted_all.extend(accepted)
            rejected_all.extend(rejected)

            # Stop as soon as we have an active strategy fitting the regime.
            got_fit = any(
                a.get("status") == "active"
                and any(p in (a.get("type", "").lower()) for p in _REGIME_TYPES[_regime_key(regime)])
                for a in accepted
            )
            if got_fit or not need_search:
                break

        await self._publish_active_strategy(market_ctx.get("symbol", "XAUUSD"))

        summary = {
            "ts": started,
            "duration_ms": int((time.time() - started) * 1000),
            "proposed": total_proposed,
            "accepted": accepted_all,
            "rejected": rejected_all,
            "regime": regime,
            "trend": market_ctx.get("trend_direction", "neutral"),
            "searched": need_search,
            "rounds_used": rounds_used,
            "improved": improved,
            "had_fitting_active": bool(fitting_active),
        }
        await self.registry.log_research_run(summary)
        await self._announce(summary)
        return summary

    async def _improve_fitting(
        self,
        existing: list[StrategyRecord],
        regime: str,
        market_ctx: dict[str, Any],
    ) -> list[str]:
        """Improve up to 2 regime-fitting strategies that aren't active yet.

        Creates a variant on an alternate timeframe (which the backtester
        evaluates differently) and keeps it if it now passes promotion. Returns
        the ids of strategies that were improved to ``active``.
        """
        candidates = [
            r for r in existing
            if r.status in ("investigating", "backtested", "paused")
            and _strategy_fits_regime(r, regime)
        ]
        # Best-first: those closest to passing (highest profit factor).
        candidates.sort(key=lambda r: (r.profit_factor or 0), reverse=True)
        improved_ids: list[str] = []
        for rec in candidates[:2]:
            try:
                alt_tfs = ["M5", "M15", "H1"]
                current_tf = (rec.timeframes or ["M15"])[0]
                new_tf = next((t for t in alt_tfs if t != current_tf), "M15")
                variant = StrategyRecord(
                    id=self.registry.new_id(),
                    name=f"{rec.name} (opt {new_tf})"[:80],
                    short_name=(rec.short_name or rec.name)[:24],
                    type=rec.type,
                    description=f"[{_regime_key(regime)} optimised] {rec.description}"[:320],
                    pairs=rec.pairs or [market_ctx.get("symbol", "XAUUSD")],
                    timeframes=[new_tf] + [t for t in (rec.timeframes or []) if t != new_tf][:2],
                    agents=rec.agents,
                    entry_logic=rec.entry_logic,
                    exit_logic=rec.exit_logic,
                    color=rec.color,
                    status="investigating",
                    proposed_by=self.proposer_agent_id,
                    created_at=time.time(),
                )
                saved = await self._evaluate_and_save(variant)
                if saved and saved.status == "active":
                    improved_ids.append(saved.id)
            except Exception:
                await logger.awarning("research.improve_failed", id=rec.id)
        return improved_ids

    def _effective_regime(self, market_ctx: dict[str, Any]) -> str:
        """Best available regime label for research (rule-based from features)."""
        regime = str(market_ctx.get("market_regime") or market_ctx.get("regime") or "").upper()
        if regime in _REGIME_TYPES:
            return regime
        # Derive from trend + volatility hints in the context.
        trend = str(market_ctx.get("trend_direction", "neutral")).lower()
        vol = str(market_ctx.get("vol_regime", "medium")).lower()
        if vol in ("extreme",):
            return "BREAKOUT"
        if trend in ("bullish", "bearish", "up", "down"):
            return "TRENDING"
        return "RANGING"

    async def re_evaluate_all(self, *, max_eval: int = 12) -> int:
        """Re-evaluate strategies against fresh OHLCV, gently.

        Each backtest is CPU-bound (Python loops hold the GIL). To keep the
        agents event loop responsive (heartbeats, /health, /status), we:
          - cap how many strategies we re-score per pass (best/active first),
          - limit how many run concurrently (semaphore),
          - yield to the loop between evaluations.
        """
        records = await self.registry.list()
        if not records:
            return 0

        # Prioritise active + most promising strategies; cap the rest.
        records = sorted(
            records,
            key=lambda r: (r.status == "active", r.profit_factor or 0.0),
            reverse=True,
        )[:max_eval]

        sem = asyncio.Semaphore(2)

        async def _eval(rec: StrategyRecord):
            async with sem:
                out = await self._evaluate_and_save(rec)
                await asyncio.sleep(0)  # give the event loop a slice
                return out

        results = await asyncio.gather(
            *(_eval(rec) for rec in records),
            return_exceptions=True,
        )

        # After re-evaluation, update the active strategy
        try:
            await self._publish_active_strategy("XAUUSD")
        except Exception:
            pass

        return sum(1 for r in results if not isinstance(r, (Exception, type(None))))

    # ── Active strategy publication ────────────────────────────────────────

    async def _publish_active_strategy(self, symbol: str) -> None:
        """Find the best active strategy and publish it so agents can use it."""
        active = await self.registry.list(status="active")
        if not active:
            return

        # Sort by profit_factor * sharpe (composite score)
        active.sort(
            key=lambda s: (s.profit_factor or 0) * (s.sharpe or 0),
            reverse=True,
        )
        best = active[0]

        payload = {
            "id": best.id,
            "name": best.name,
            "type": best.type,
            "entry_logic": best.entry_logic,
            "exit_logic": best.exit_logic,
            "timeframes": best.timeframes,
            "win_rate": best.win_rate,
            "profit_factor": best.profit_factor,
            "sharpe": best.sharpe,
            "regime_fit": best.description[:100],
            "updated_at": time.time(),
        }
        await self.redis.cache_set(
            ACTIVE_STRATEGY_KEY.format(symbol=symbol),
            payload,
            ttl_sec=3600,
        )

    # ── Internals ──────────────────────────────────────────────────────────

    async def _evaluate_and_save(self, rec: StrategyRecord) -> StrategyRecord | None:
        try:
            metrics = await self.evaluator.evaluate(rec)
        except Exception:
            await logger.aexception("research.eval_failed", id=rec.id)
            return None

        rec.win_rate = metrics.win_rate
        rec.profit_factor = metrics.profit_factor
        rec.sharpe = metrics.sharpe
        rec.max_dd_pct = metrics.max_dd_pct
        rec.total_trades = metrics.total_trades
        rec.avg_rr = metrics.avg_rr
        rec.net_pnl_pct = metrics.net_pnl_pct
        if metrics.recent_results:
            rec.recent_results = metrics.recent_results
        if not rec.mql5_signal_code:
            rec.mql5_signal_code = _default_mql5_for(rec)

        return await self.registry.evaluate_and_promote(rec)

    async def _generate_candidates(
        self,
        *,
        n: int,
        existing_names: list[str],
        context: dict[str, Any],
        regime: str | None = None,
    ) -> list[dict[str, Any]]:
        """Try the LLM with real candle data first, fall back to templates.

        When ``regime`` is given, the fallback templates are biased toward the
        strategy types that suit that regime so we don't waste rounds proposing
        e.g. trend strategies in a ranging market.
        """
        candidates = await self._llm_candidates(
            n=n, existing_names=existing_names, context=context,
        )
        if candidates:
            return candidates
        await logger.ainfo("research.fallback_templates", regime=regime)
        return _fallback_templates(
            n=n,
            existing_names=existing_names,
            regime=(regime or context.get("vol_regime", "medium")),
        )

    async def _fetch_full_context(self) -> dict[str, Any]:
        """Gather OHLCV candles + indicators + MTF context for the LLM."""
        symbol = os.getenv("NEXUS_RESEARCH_SYMBOL", "XAUUSD")
        ctx: dict[str, Any] = {"symbol": symbol}

        # 1. Fetch recent OHLCV candles (the real data the LLM sees)
        for tf in ("M15", "H1", "M5"):
            try:
                import redis.asyncio as aioredis
                redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
                r = aioredis.from_url(redis_url, decode_responses=True)
                raw = await r.get(f"nexus:ohlcv:{symbol}:{tf}")
                await r.aclose()
                if raw:
                    candles = json.loads(raw)
                    if isinstance(candles, list) and len(candles) >= 30:
                        ctx["candles"] = candles[-50:]  # last 50 candles
                        ctx["candle_tf"] = tf
                        break
            except Exception:
                pass

        # 2. Current indicators from features cache
        for tf in ("M15", "H1"):
            try:
                features_raw = await self.redis.cache_get(
                    f"features:{symbol}:{tf}", prefix=False,
                )
                if features_raw and isinstance(features_raw, dict):
                    feats = features_raw.get("features", {})
                    ctx["indicators"] = feats
                    ctx["vol_regime"] = feats.get("vol_regime", "medium")
                    ctx["trend_direction"] = feats.get("trend_direction", "neutral")
                    ctx["trend_strength"] = feats.get("trend_strength", 0)
                    ctx["atr"] = feats.get("atr_14", feats.get("atr", 0))
                    ctx["atr_norm"] = feats.get("atr_normalized", 0)
                    ctx["session_name"] = feats.get("session", "unknown")
                    break
            except Exception:
                pass

        # 3. Multi-timeframe context (H1, H4, D1)
        mtf_parts: list[str] = []
        for htf in ("H1", "H4", "D1"):
            try:
                htf_raw = await self.redis.cache_get(
                    f"features:{symbol}:{htf}", prefix=False,
                )
                if htf_raw and isinstance(htf_raw, dict):
                    hf = htf_raw.get("features", {})
                    mtf_parts.append(
                        f"{htf}: RSI={hf.get('rsi', '?')}, "
                        f"EMA9={hf.get('ema_9', '?')}, EMA21={hf.get('ema_21', '?')}, "
                        f"EMA50={hf.get('ema_50', '?')}, EMA200={hf.get('ema_200', '?')}, "
                        f"MACD_hist={hf.get('macd_histogram', '?')}, "
                        f"trend={hf.get('trend_direction', '?')}"
                    )
            except Exception:
                pass
        ctx["mtf_context"] = "\n".join(mtf_parts) if mtf_parts else "HTF data unavailable"

        # 4. Session info
        try:
            session_raw = await self.redis.cache_get("session:state")
            if session_raw:
                ctx["session_name"] = ctx.get("session_name") or session_raw.get("name", "unknown")
        except Exception:
            pass

        # 5. Recent trade results — pull from TradeMemory for rich feedback
        try:
            import redis.asyncio as _aioredis
            import json as _json
            _redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            _r = _aioredis.from_url(_redis_url, decode_responses=True)
            _metrics_raw = await _r.get("nexus:trade_memory:metrics")
            _trades_raw = await _r.get("nexus:trade_memory:trades")
            await _r.aclose()

            if _metrics_raw:
                _m = _json.loads(_metrics_raw)
                _trades = _json.loads(_trades_raw) if _trades_raw else []
                _streak = _m.get("recent_streak", {})
                _by_sess = _m.get("by_session", {})
                _by_regime = _m.get("by_regime", {})
                _by_src = _m.get("by_source", {})

                # Format session breakdown
                _sess_lines = []
                for _s, _sd in _by_sess.items():
                    _sess_lines.append(
                        f"  {_s}: WR={_sd.get('win_rate', 0):.0%} "
                        f"({_sd.get('trades', 0)} trades, avg {_sd.get('avg_pnl', 0):+.1f} pips)"
                    )

                ctx["recent_results"] = (
                    f"Total trades in memory: {_m.get('total_trades', 0)} | "
                    f"Overall WR: {_m.get('overall_win_rate', 0):.0%} | "
                    f"Avg PnL: {_m.get('avg_pnl_pips', 0):+.1f} pips\n"
                    f"Streak: {_streak.get('count', 0)} consecutive {_streak.get('type', 'none')}s\n"
                    f"By session:\n" + "\n".join(_sess_lines or ["  No session data"]) + "\n"
                    f"By regime: " + ", ".join(
                        f"{_rk}={_rv.get('win_rate', 0):.0%}({_rv.get('trades', 0)}t)"
                        for _rk, _rv in _by_regime.items()
                    ) + "\n"
                    f"By source: " + ", ".join(
                        f"{_sk}={_sv.get('win_rate', 0):.0%}({_sv.get('trades', 0)}t)"
                        for _sk, _sv in _by_src.items()
                    )
                )
            else:
                ctx["recent_results"] = "No trade memory data yet — system is accumulating trades."
        except Exception:
            try:
                from ..tools.risk_tools import check_exposure
                exposure = await check_exposure()
                ctx["recent_results"] = (
                    f"Daily PnL: ${exposure.get('daily_pnl', 0):.2f}, "
                    f"Consecutive losses: {exposure.get('consecutive_losses', 0)}, "
                    f"Daily loss: {exposure.get('daily_loss_pct', 0):.1f}%"
                )
            except Exception:
                ctx["recent_results"] = "Trade data unavailable."

        return ctx

    def _format_candles_for_llm(self, candles: list[dict], limit: int = 30) -> str:
        """Format OHLCV candles as a compact table for the LLM."""
        if not candles:
            return "No candle data available"

        recent = candles[-limit:]
        lines = ["idx | open     | high     | low      | close    | volume"]
        lines.append("--- | -------- | -------- | -------- | -------- | ------")
        for i, c in enumerate(recent):
            lines.append(
                f"{i:3d} | {c.get('open', 0):>8.2f} | {c.get('high', 0):>8.2f} | "
                f"{c.get('low', 0):>8.2f} | {c.get('close', 0):>8.2f} | "
                f"{c.get('volume', 0):>6.0f}"
            )
        return "\n".join(lines)

    def _format_indicators(self, indicators: dict) -> str:
        """Format indicators as key=value pairs."""
        if not indicators:
            return "No indicator data"
        important = [
            "close", "rsi", "ema_9", "ema_21", "ema_50", "ema_200",
            "macd", "macd_histogram", "macd_signal", "adx", "di_plus", "di_minus",
            "bb_upper", "bb_lower", "bb_pct_b", "bb_bandwidth",
            "stoch_k", "stoch_d", "atr_14", "atr_normalized",
            "trend_direction", "trend_strength", "trend_score", "vol_regime",
        ]
        parts = []
        for k in important:
            v = indicators.get(k)
            if v is not None:
                if isinstance(v, float):
                    parts.append(f"{k}={v:.4f}")
                else:
                    parts.append(f"{k}={v}")
        return ", ".join(parts)

    async def _llm_candidates(
        self,
        *,
        n: int,
        existing_names: list[str],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not _llm_configured():
            return []

        try:
            from ..agents.base_agent import BaseAgent

            class _Researcher(BaseAgent):
                async def invoke(self, state):
                    return state

            agent = _Researcher(
                self.proposer_agent_id,
                "Strategy Researcher",
                redis_client=self.redis,
            )
            llm = agent._get_llm(temperature=0.6, max_tokens=2000)
        except Exception:
            await logger.aexception("research.llm_init_failed")
            return []

        symbol = context.get("symbol", "XAUUSD")
        candles = context.get("candles", [])
        candle_tf = context.get("candle_tf", "M15")
        indicators = context.get("indicators", {})

        # Format existing strategies with their performance
        existing_records = await self.registry.list()
        existing_str = ""
        for rec in existing_records[:10]:
            existing_str += (
                f"- {rec.name} ({rec.status}): WR={rec.win_rate:.0%}, "
                f"PF={rec.profit_factor:.2f}, Sharpe={rec.sharpe:.2f}, "
                f"Trades={rec.total_trades}\n"
            )
        if not existing_str:
            existing_str = "No existing strategies yet."

        # Format recent results. _fetch_full_context() already renders this into
        # a human-readable string for the LLM prompt, so use it as-is. (A dict is
        # tolerated for safety in case another caller supplies raw metrics.)
        recent = context.get("recent_results")
        if isinstance(recent, dict):
            recent_str = (
                f"Daily PnL: ${recent.get('daily_pnl', 0):.2f}, "
                f"Consecutive losses: {recent.get('consecutive_losses', 0)}, "
                f"Daily loss: {recent.get('daily_loss_pct', 0):.1f}%"
            )
        elif isinstance(recent, str) and recent.strip():
            recent_str = recent
        else:
            recent_str = "No recent trade data."

        # Determine timeframes to suggest based on regime
        vol_regime = context.get("vol_regime", "medium")
        if vol_regime in ("high", "extreme"):
            suggested_tfs = ["M15", "H1"]
        elif vol_regime == "low":
            suggested_tfs = ["M5", "M15"]
        else:
            suggested_tfs = ["M15", "H1"]

        prompt = _RESEARCH_PROMPT.format(
            symbol=symbol,
            candle_count=len(candles),
            timeframe=candle_tf,
            candles=self._format_candles_for_llm(candles, limit=30),
            indicators=self._format_indicators(indicators),
            mtf_context=context.get("mtf_context", "HTF data unavailable"),
            vol_regime=vol_regime,
            trend_direction=context.get("trend_direction", "neutral"),
            trend_strength=context.get("trend_strength", 0),
            session=context.get("session_name", "unknown"),
            atr=context.get("atr", 0),
            atr_norm=context.get("atr_norm", 0),
            existing=existing_str,
            recent_results=recent_str,
            n=n,
            timeframes_json=json.dumps(suggested_tfs),
        )

        try:
            resp = await asyncio.wait_for(
                asyncio.to_thread(llm.invoke, prompt), timeout=60.0,
            )
        except Exception:
            await logger.aexception("research.llm_call_failed")
            return []

        content = getattr(resp, "content", "") or str(resp)
        return _parse_strategy_list(content)

    def _spec_to_record(
        self, spec: dict[str, Any], context: dict[str, Any] | None = None,
    ) -> StrategyRecord:
        ctx = context or {}
        name = str(spec.get("name", "")).strip() or f"Strategy {self.registry.new_id()}"
        short = str(spec.get("short_name") or name)[:24]
        ttype = str(spec.get("type") or "Trend Following")
        desc = str(spec.get("description") or "")[:320]

        # Append regime info to description if available
        regime_fit = spec.get("regime_fit", ctx.get("vol_regime", ""))
        htf_align = spec.get("htf_alignment", "neutral")
        if regime_fit and regime_fit not in desc.lower():
            desc = f"[{regime_fit.upper()} regime, {htf_align}] {desc}"[:320]

        pairs = [p.upper().replace("/", "") for p in spec.get("pairs", []) if p][:6]
        pairs = pairs or [ctx.get("symbol", "XAUUSD")]
        tfs = [t for t in spec.get("timeframes", []) if t][:4] or ["M15"]
        agents = [a for a in spec.get("agents", []) if a][:8] or ["A1", "A7", "A8"]
        entry = str(spec.get("entry_logic") or "")[:280]
        exit_ = str(spec.get("exit_logic") or "")[:280]

        rec = StrategyRecord(
            id=self.registry.new_id(),
            name=name,
            short_name=short,
            type=ttype,
            description=desc,
            pairs=pairs,
            timeframes=tfs,
            agents=agents,
            entry_logic=entry,
            exit_logic=exit_,
            color=_color_for(ttype),
            status="investigating",
            proposed_by=self.proposer_agent_id,
            created_at=time.time(),
        )
        rec.mql5_signal_code = _default_mql5_for(rec)
        return rec

    async def _announce(self, summary: dict[str, Any]) -> None:
        """Broadcast the run on Redis so the dashboard can react."""
        try:
            await self.redis.publish_channel("strategy:research", summary)
        except Exception:
            await logger.awarning("research.publish_failed")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _llm_configured() -> bool:
    if os.getenv("NEXUS_DISABLE_RESEARCH_LLM", "").lower() in ("1", "true", "yes"):
        return False
    if os.getenv("NEXUS_LLM_PROVIDER", "cloud") == "local":
        return True
    return bool(os.getenv("DEEPSEEK_API_KEY"))


def _parse_strategy_list(content: str) -> list[dict[str, Any]]:
    """Extract a JSON ``strategies`` list even if the model wraps it in fences."""
    if not content:
        return []

    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return []
    blob = cleaned[start: end + 1]

    try:
        data = json.loads(blob)
    except Exception:
        return []
    items = data.get("strategies") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [s for s in items if isinstance(s, dict)]


def _color_for(ttype: str) -> str:
    t = (ttype or "").lower()
    if "trend" in t or "ema" in t:
        return "cyan"
    if "session" in t or "breakout" in t:
        return "gold"
    if "reversion" in t or "bounce" in t or "mean" in t:
        return "green"
    if "news" in t or "event" in t:
        return "red"
    return "purple"


def _fallback_templates(
    *,
    n: int,
    existing_names: list[str],
    regime: str = "medium",
) -> list[dict[str, Any]]:
    """Return ``n`` regime-appropriate specs not already in the registry."""
    # Templates are tagged by regime fitness
    catalogue: list[dict[str, Any]] = [
        {
            "name": "RSI Divergence with ATR Confirmation",
            "short_name": "RSI Divergence",
            "type": "Mean Reversion",
            "description": "Detects bullish/bearish RSI divergence on H1 confirmed by an ATR expansion candle.",
            "pairs": ["XAUUSD"],
            "timeframes": ["H1"],
            "agents": ["A1", "A4", "A7", "A8"],
            "entry_logic": "RSI divergence + ATR expansion >1.5x avg + conviction>=60",
            "exit_logic": "TP1 1:1 (50%), TP2 1.5:1 (30%), trail (20%)",
            "regime_fit": "medium",
        },
        {
            "name": "MACD Histogram Pullback",
            "short_name": "MACD Pullback",
            "type": "Trend Following",
            "description": "Pullbacks during established trend when MACD histogram flips back to trend direction.",
            "pairs": ["XAUUSD"],
            "timeframes": ["M15", "H1"],
            "agents": ["A1", "A3", "A7", "A8"],
            "entry_logic": "Trend EMA50 alignment + MACD histogram flip + close beyond pivot",
            "exit_logic": "TP1 1:1, TP2 1.5:1, TP3 trail; time stop",
            "regime_fit": "medium",
        },
        {
            "name": "Bollinger Squeeze Expansion",
            "short_name": "BB Squeeze",
            "type": "Breakout",
            "description": "Trades the first close outside bands after a squeeze (lowest BB width percentile).",
            "pairs": ["XAUUSD"],
            "timeframes": ["M15", "H1"],
            "agents": ["A1", "A5", "A7", "A8"],
            "entry_logic": "BB width 30-bar percentile <10 then close outside bands",
            "exit_logic": "TP1 1:1, TP2 1.5:1, trail 0.5*ATR",
            "regime_fit": "low",
        },
        {
            "name": "High-Vol ATR Breakout",
            "short_name": "ATR Breakout",
            "type": "Breakout",
            "description": "Enters on ATR expansion >2x avg during high-vol regime, tight SL with wide TP.",
            "pairs": ["XAUUSD"],
            "timeframes": ["M15"],
            "agents": ["A1", "A4", "A7", "A8"],
            "entry_logic": "ATR > 2x avg + volume spike + EMA alignment + momentum confirmation",
            "exit_logic": "TP1 1.5:1, TP2 2.5:1, aggressive trail 0.3*ATR",
            "regime_fit": "high",
        },
        {
            "name": "Session Open Reversal",
            "short_name": "Session Reversal",
            "type": "Session-Based",
            "description": "Fades the first 15-min push after London/NY open if it reverses back.",
            "pairs": ["XAUUSD"],
            "timeframes": ["M5", "M15"],
            "agents": ["A1", "A2", "A7", "A8"],
            "entry_logic": "First 15min candle reverses into prior range + RSI divergence",
            "exit_logic": "TP at range mid, hard exit 30min",
            "regime_fit": "medium",
        },
        {
            "name": "Extreme Vol Mean Reversion",
            "short_name": "ExtVol MR",
            "type": "Mean Reversion",
            "description": "Quick reversal entries when ATR spikes to extreme with RSI at extremes.",
            "pairs": ["XAUUSD"],
            "timeframes": ["M5", "M15"],
            "agents": ["A1", "A7", "A8"],
            "entry_logic": "RSI <25 or >75 + ATR extreme regime + volume exhaustion pattern",
            "exit_logic": "Quick TP1 0.5:1, TP2 1:1, tight trail, 15min time exit",
            "regime_fit": "extreme",
        },
    ]

    # Prioritise templates matching current regime
    regime_priority = {"exact": 0, "close": 1, "other": 2}
    regime_map = {
        "low": ["low", "medium"],
        "medium": ["medium", "low", "high"],
        "high": ["high", "extreme", "medium"],
        "extreme": ["extreme", "high"],
    }
    preferred = regime_map.get(regime, ["medium"])

    def _sort_key(spec: dict) -> int:
        fit = spec.get("regime_fit", "medium")
        if fit == regime:
            return 0
        if fit in preferred:
            return 1
        return 2

    catalogue.sort(key=_sort_key)

    out: list[dict[str, Any]] = []
    seen = {n.lower() for n in existing_names}
    for spec in catalogue:
        if spec["name"].lower() in seen:
            continue
        out.append(spec)
        if len(out) >= n:
            break
    return out


def _default_mql5_for(rec: StrategyRecord) -> str:
    t = (rec.type or "").lower()
    if "trend" in t or "ema" in t:
        return _MQL5_TREND
    if "session" in t or "breakout" in t:
        return _MQL5_BREAKOUT
    if "reversion" in t or "bounce" in t or "mean" in t:
        return _MQL5_REVERSION
    return _MQL5_GENERIC


_MQL5_TREND = """// Trend / EMA confluence signal
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

_MQL5_BREAKOUT = """// Breakout signal
int GetDirection() {
   double hi = iHigh(_Symbol, PERIOD_M15, iHighest(_Symbol, PERIOD_M15, MODE_HIGH, 20, 1));
   double lo = iLow (_Symbol, PERIOD_M15, iLowest (_Symbol, PERIOD_M15, MODE_LOW,  20, 1));
   double c  = iClose(_Symbol, PERIOD_M15, 0);
   if (c > hi) return  1;
   if (c < lo) return -1;
   return 0;
}
int GetConviction() { return 60; }
"""

_MQL5_REVERSION = """// Mean-reversion signal
int GetDirection() {
   double rsi = iRSI(_Symbol, PERIOD_H1, 14, PRICE_CLOSE, 0);
   double atr = iATR(_Symbol, PERIOD_H1, 14, 0);
   double prev = iATR(_Symbol, PERIOD_H1, 14, 1);
   bool exp = atr > prev * 1.2;
   if (rsi < 30 && exp) return  1;
   if (rsi > 70 && exp) return -1;
   return 0;
}
int GetConviction() { return 60; }
"""

_MQL5_GENERIC = """// Generic momentum signal
int GetDirection() {
   double c0 = iClose(_Symbol, PERIOD_CURRENT, 0);
   double c1 = iClose(_Symbol, PERIOD_CURRENT, 1);
   double c5 = iClose(_Symbol, PERIOD_CURRENT, 5);
   if (c0 > c1 && c0 > c5) return  1;
   if (c0 < c1 && c0 < c5) return -1;
   return 0;
}
int GetConviction() { return 55; }
"""


__all__ = ["StrategyResearcher"]
