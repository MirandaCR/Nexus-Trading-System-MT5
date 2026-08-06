"""
A7 — El Juez: Arbitrator agent.

Weighs all evidence from the bull vs bear debate, checks consistency across
analysts, applies Bayesian reasoning, and produces the final trade decision.

Rules:
  - BUY/SELL only if conviction >= 70
  - Conflict lock: both sides >= 92 AND gap < 12pts → NO_TRADE (genuine conflict)
  - If gap >= 12pts the dominant side wins even with high overall conviction
  - Returns verdict, verdict_conviction, and verdict_reasoning
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

import structlog

from shared.redis_client import RedisClient

from ..state import AgentState
from .base_agent import BaseAgent

logger = structlog.get_logger(__name__)
_ACTIONABLE_CONVICTION = int(os.getenv("NEXUS_MIN_VERDICT_CONVICTION", "70"))
_CONFLICT_LOCK_CONVICTION = int(os.getenv("NEXUS_CONFLICT_LOCK_CONVICTION", "92"))
_CONFLICT_MIN_GAP = int(os.getenv("NEXUS_CONFLICT_MIN_GAP", "12"))


class ArbitratorAgent(BaseAgent):
    def __init__(self, *, redis_client: RedisClient | None = None) -> None:
        super().__init__("A7", "Arbitrator", redis_client=redis_client)

    async def invoke(self, state: AgentState) -> AgentState:
        symbol = state["symbol"]
        bull_conv = state.get("bull_conviction", 0)
        bear_conv = state.get("bear_conviction", 0)

        htf_ctx = state.get("htf_context", {})
        htf_bias = htf_ctx.get("bias", "NEUTRAL")
        htf_alignment = htf_ctx.get("alignment_score", 0)

        # Fetch active strategy from research lab (if any)
        active_strategy_block = ""
        try:
            if self.redis:
                active_strat = await self.redis.cache_get(
                    f"strategy:active:{symbol}",
                )
                if active_strat and isinstance(active_strat, dict):
                    active_strategy_block = (
                        f"\n=== ACTIVE STRATEGY (from Research Lab) ===\n"
                        f"Name: {active_strat.get('name', 'N/A')}\n"
                        f"Type: {active_strat.get('type', 'N/A')}\n"
                        f"Entry logic: {active_strat.get('entry_logic', 'N/A')}\n"
                        f"Exit logic: {active_strat.get('exit_logic', 'N/A')}\n"
                        f"Backtest: WR={active_strat.get('win_rate', 0):.0%}, "
                        f"PF={active_strat.get('profit_factor', 0):.2f}, "
                        f"Sharpe={active_strat.get('sharpe', 0):.2f}\n"
                        f"NOTE: If current signals align with this tested strategy, "
                        f"it increases trade confidence.\n"
                    )
        except Exception:
            pass

        # ── Institutional features (pre-computed by nexus_features) ──────
        inst_data: dict = {}
        inst_block = ""
        _inst_conv_adjustment = 0
        try:
            if self.redis:
                raw_inst = await self.redis.r.get(
                    f"nexus:features:institutional:{symbol}"
                )
                if raw_inst:
                    inst_data = json.loads(raw_inst)
                    H = inst_data.get("hurst", {}).get("H", 0.5)
                    h_regime = inst_data.get("hurst", {}).get("regime", "NOISY")
                    pe = inst_data.get("perm_entropy", {}).get("permutation_entropy", 1.0)
                    score = inst_data.get("operability_score", 50)
                    rec = inst_data.get("recommendation", "WAIT")
                    ti = inst_data.get("tick_imbalance", {})
                    rv = inst_data.get("rv_ratio", {})
                    cp = inst_data.get("change_points", {})
                    tg = inst_data.get("temporal_growth", {})
                    ek = inst_data.get("ergodic_kelly", {})

                    # Net conviction adjustment from entropy
                    _inst_conv_adjustment = int(
                        inst_data.get("perm_entropy", {}).get("conviction_adjustment", 0)
                    )
                    # Noisy Hurst → push toward WAIT
                    if h_regime == "NOISY":
                        _inst_conv_adjustment += 15

                    inst_block = (
                        f"\n=== INSTITUTIONAL FEATURES (matemática cuantitativa) ===\n"
                        f"Operability score: {score:.0f}/100 → {rec}\n"
                        f"Hurst H={H:.3f} → {h_regime} (preferred: {inst_data.get('hurst', {}).get('preferred_model', '?')})\n"
                        f"Permutation entropy: {pe:.3f} (0=ordered, 1=caótico)\n"
                        f"Tick imbalance: {ti.get('tick_imbalance', 0):.3f} ({ti.get('directional_bias', 'NEUTRAL')})\n"
                        f"RV ratio: {rv.get('rv_ratio', 1.0):.2f} ({rv.get('regime', 'STABLE')})\n"
                        f"Kelly ergódico lot: {ek.get('lot', 0.01):.2f} "
                        f"(classic={ek.get('kelly_classic', 0):.3f}, "
                        f"ergodic={ek.get('kelly_ergodic', 0):.3f})\n"
                        f"Temporal growth g: {tg.get('temporal_growth_g', 'N/A')} → {tg.get('action', 'UNKNOWN')}\n"
                    )
                    if cp.get("recent_shift"):
                        inst_block += f"⚠ Regime change {cp.get('bars_since', '?')} bars ago — ensemble weight reduced\n"
                    if inst_data.get("lyapunov", {}).get("reliable"):
                        lhb = inst_data["lyapunov"]["horizon_bars"]
                        inst_block += f"Lyapunov horizon: {lhb} bars ({lhb * 5} min M5) — max valid projection\n"
                    if _inst_conv_adjustment != 0:
                        inst_block += (
                            f"→ Ajuste automático de conviction threshold: "
                            f"{'+' if _inst_conv_adjustment > 0 else ''}{_inst_conv_adjustment}pts\n"
                            f"  Umbral efectivo: {_ACTIONABLE_CONVICTION + _inst_conv_adjustment}\n"
                        )
        except Exception:
            pass

        # ── Regime context ──
        regime = state.get("regime_analysis", {})
        regime_type = regime.get("regime", "UNKNOWN")
        regime_strength = regime.get("regime_strength", 0)
        regime_approach = regime.get("suggested_approach", "")
        range_info = regime.get("range_info", {})

        regime_block = (
            f"=== MARKET REGIME (A0) ===\n"
            f"Regime: {regime_type} (strength={regime_strength}/100)\n"
            f"Suggested approach: {regime_approach}\n"
            f"Volatility: {regime.get('vol_regime', 'MEDIUM')}\n"
        )
        if range_info:
            regime_block += (
                f"Range: {range_info.get('range_low', 0):.5f} — "
                f"{range_info.get('range_high', 0):.5f}\n"
                f"Position in range: {range_info.get('position_in_range', 0):.1%}\n"
                f"Mean-reversion setup: {range_info.get('mean_reversion_setup', 'NONE')}\n"
            )

        # ── Trade memory context ──
        trade_mem = state.get("trade_memory", {})
        memory_block = ""
        if trade_mem and trade_mem.get("total_trades", 0) >= 5:
            overall_wr = trade_mem.get("overall_win_rate", 0)
            streak = trade_mem.get("recent_streak", {})
            sess_data = trade_mem.get("by_session", {}).get(
                state.get("session", "").upper(), {}
            )
            memory_block = (
                f"\n=== TRADE MEMORY (recent {trade_mem.get('total_trades', 0)} trades) ===\n"
                f"Overall win rate: {overall_wr:.0%}\n"
                f"Recent streak: {streak.get('count', 0)} {streak.get('type', 'none')}\n"
            )
            if sess_data:
                memory_block += (
                    f"Session {state.get('session', '')} win rate: "
                    f"{sess_data.get('win_rate', 0):.0%} ({sess_data.get('trades', 0)} trades)\n"
                )
            by_dir = trade_mem.get("by_direction", {})
            for d in ("BUY", "SELL"):
                dd = by_dir.get(d, {})
                if dd.get("trades", 0) > 0:
                    memory_block += f"  {d}: WR={dd.get('win_rate', 0):.0%} ({dd.get('trades', 0)} trades)\n"
            memory_block += (
                "NOTE: If recent performance for the current session, direction, or regime "
                "is poor, reduce conviction or skip the trade.\n"
            )

        context_block = (
            f"Symbol: {symbol}\n"
            f"Session: {state.get('session', 'UNKNOWN')}\n\n"
            f"{regime_block}\n"
            f"{memory_block}"
            f"=== MULTI-TIMEFRAME CONTEXT ===\n"
            f"HTF Bias: {htf_bias} (alignment score: {htf_alignment}/4)\n"
            f"Timeframes analysed: {htf_ctx.get('htf_list', [])}\n"
            f"HTF Details: {json.dumps(htf_ctx.get('htf_details', {}), default=str)}\n\n"
            f"=== TECHNICAL ANALYSIS (A1) ===\n"
            f"{state.get('technical_analysis', 'Not available')[:500]}\n\n"
            f"=== NEWS ASSESSMENT (A2) ===\n"
            f"{state.get('news_assessment', 'Not available')[:500]}\n\n"
            f"=== INTERMARKET SCORE (A3) ===\n"
            f"Score: {state.get('intermarket_score', 0.0)}/10\n\n"
            f"=== ORDER FLOW SCORE (A4) ===\n"
            f"Score: {state.get('flow_score', 0.0)}/10\n\n"
            f"=== BULL CASE (A5) ===\n"
            f"Conviction: {bull_conv}/100\n"
            f"{state.get('bull_case', 'Not available')[:500]}\n\n"
            f"=== BEAR CASE (A6) ===\n"
            f"Conviction: {bear_conv}/100\n"
            f"{state.get('bear_case', 'Not available')[:500]}\n\n"
            f"=== ML SIGNAL (Quantitative Model) ===\n"
            f"Direction: {state.get('ml_signal', {}).get('direction', 'NO_TRADE')}\n"
            f"Confidence: {state.get('ml_signal', {}).get('confidence', 0)}\n"
            f"Scores: {json.dumps(state.get('ml_signal', {}).get('ml_scores', {}), default=str)}\n"
            f"NOTE: ML confidence >= 0.80 with aligned technicals is a high-conviction setup.\n"
            f"{active_strategy_block}"
            f"{inst_block}\n"
            f"RULES:\n"
            f"1. If both bull and bear conviction >= 65, verdict MUST be NO_TRADE (conflicting signals).\n"
            f"2. BUY or SELL if your conviction >= {_ACTIONABLE_CONVICTION + _inst_conv_adjustment} "
            f"(threshold adjusted by institutional metrics: base={_ACTIONABLE_CONVICTION}, adjustment={_inst_conv_adjustment:+d}).\n"
            f"3. WATCH if there's a potential setup but not ready yet.\n"
            f"4. A strong ML signal aligned with technicals warrants a trade.\n"
            f"5. COUNTER-TREND TRADES (trading against HTF bias with alignment >= 3) "
            f"require HIGHER conviction (75+) and should be penalised.\n"
            f"6. WITH-TREND trades (aligned with HTF bias) get a conviction BONUS.\n"
            f"7. If an active strategy's entry logic matches current conditions, boost conviction.\n"
            f"8. ** REGIME-AWARE RULES **:\n"
            f"   - RANGING regime: Consider mean-reversion trades (buy at support, sell at resistance).\n"
            f"     Both bull AND bear may have moderate conviction — this is normal in a range.\n"
            f"     If price is near range boundary with RSI confirmation, a trade IS valid.\n"
            f"   - BREAKOUT regime: Only trade in breakout direction with volume confirmation.\n"
            f"   - TRENDING regime: Standard trend-following rules apply.\n\n"
            f"Respond with EXACTLY:\n"
            f"VERDICT: <BUY|SELL|NO_TRADE|WATCH>\n"
            f"CONVICTION: <0-100>\n"
            f"REASONING: <your detailed reasoning>"
        )

        try:
            llm = self._get_llm()
            prompt = self._load_prompt("arbitrator")
            chain = prompt | llm
            response = await self._invoke_with_budget(chain, {"input": context_block})
            raw_output = response.content if hasattr(response, "content") else str(response)

            verdict, conviction, reasoning = self._parse_verdict(raw_output)
        except Exception as exc:
            verdict, conviction, reasoning = self._heuristic_verdict(state)
            raw_output = reasoning
            state["errors"].append(f"A7: LLM call failed: {exc}")

        _conv_gap = abs(bull_conv - bear_conv)
        _both_high = max(bull_conv, bear_conv) >= _CONFLICT_LOCK_CONVICTION
        _genuine_conflict = _both_high and _conv_gap < _CONFLICT_MIN_GAP
        if _genuine_conflict:
            # In ranging markets, high conviction on both sides is expected —
            # allow mean-reversion trades if regime supports it
            if regime_type == "RANGING" and range_info:
                mrs = range_info.get("mean_reversion_setup", "WAIT")
                pos_in_range = range_info.get("position_in_range", 0.5)
                if mrs in ("BUY", "SELL") and (pos_in_range < 0.2 or pos_in_range > 0.8):
                    verdict = mrs
                    conviction = min(70, max(bull_conv, bear_conv))
                    reasoning = (
                        f"RANGE TRADE: Both bull ({bull_conv}) and bear ({bear_conv}) "
                        f"have high conviction (gap={_conv_gap}pts, expected in range). "
                        f"Mean-reversion {mrs} at range boundary "
                        f"(position={pos_in_range:.0%}). {reasoning[:200]}"
                    )
                else:
                    verdict = "NO_TRADE"
                    reasoning = (
                        f"RANGING market: bull ({bull_conv}) and bear ({bear_conv}) both high "
                        f"(gap={_conv_gap}pts < {_CONFLICT_MIN_GAP} threshold), "
                        f"but price not at range boundary (pos={pos_in_range:.0%}). "
                        f"Wait for extremes. {reasoning[:200]}"
                    )
            else:
                verdict = "NO_TRADE"
                reasoning = (
                    f"Genuine conflict: bull={bull_conv} bear={bear_conv} gap={_conv_gap}pts "
                    f"(< {_CONFLICT_MIN_GAP} minimum gap required). "
                    f"NO_TRADE enforced. Original reasoning: {reasoning[:300]}"
                )

        ml_signal = state.get("ml_signal", {})
        ml_conf = float(ml_signal.get("confidence", 0))
        ml_dir = ml_signal.get("direction", "NO_TRADE")
        if verdict in ("NO_TRADE", "WATCH") and ml_conf >= 0.75 and ml_dir in ("BUY", "SELL"):
            h_verdict, h_conviction, h_reasoning = self._heuristic_verdict(state)
            if h_verdict == ml_dir and h_conviction >= _ACTIONABLE_CONVICTION:
                verdict = h_verdict
                conviction = h_conviction
                reasoning = (
                    f"LLM said NO_TRADE but ML+heuristic agree on {ml_dir} (conf={ml_conf:.2f}). "
                    f"Overridden: {h_reasoning}"
                )
            elif ml_conf >= 0.85:
                verdict = ml_dir
                conviction = min(100, int(ml_conf * 75))
                reasoning = (
                    f"LLM said NO_TRADE but ML signal is very strong ({ml_dir} conf={ml_conf:.2f}). "
                    f"Using ML direction directly."
                )

        # ── Multi-timeframe conviction adjustment ──
        # Trades aligned with HTF get a bonus; counter-trend trades are penalised.
        if verdict in ("BUY", "SELL") and abs(htf_alignment) >= 2:
            is_aligned = (
                (verdict == "BUY" and htf_bias == "BULLISH")
                or (verdict == "SELL" and htf_bias == "BEARISH")
            )
            is_counter = (
                (verdict == "BUY" and htf_bias == "BEARISH")
                or (verdict == "SELL" and htf_bias == "BULLISH")
            )

            if is_aligned:
                bonus = min(10, abs(htf_alignment) * 3)
                conviction = min(100, conviction + bonus)
                reasoning = (
                    f"[MTF +{bonus} bonus: {verdict} aligned with HTF {htf_bias} "
                    f"(score={htf_alignment})]. {reasoning}"
                )
            elif is_counter:
                penalty = abs(htf_alignment) * 4
                conviction = max(0, conviction - penalty)
                reasoning = (
                    f"[MTF -{penalty} penalty: {verdict} AGAINST HTF {htf_bias} "
                    f"(score={htf_alignment})]. {reasoning}"
                )
                # Strong counter-trend filter: if alignment is extreme, reject
                if abs(htf_alignment) >= 4 and conviction < 75:
                    verdict = "WATCH"
                    reasoning = (
                        f"Counter-trend trade blocked: {verdict} vs strong HTF "
                        f"{htf_bias} (alignment={htf_alignment}). {reasoning}"
                    )

        # ── Active strategy alignment (deterministic, not just LLM text) ──
        if verdict in ("BUY", "SELL"):
            try:
                active_strat = None
                if self.redis:
                    active_strat = await self.redis.cache_get(f"strategy:active:{symbol}")
                if active_strat and isinstance(active_strat, dict):
                    from shared.strategy_engine import (
                        score_strategy_alignment,
                        should_boost_conviction,
                    )
                    align_score, align_reasons = score_strategy_alignment(
                        active_strat,
                        verdict=verdict,
                        regime=regime_type,
                        session=state.get("session", ""),
                        htf_bias=htf_bias,
                        ml_direction=str(ml_signal.get("direction", "")),
                    )
                    boost = should_boost_conviction(align_score)
                    if boost != 0:
                        conviction = max(0, min(100, conviction + boost))
                        reasoning = (
                            f"[Strategy align={align_score:.2f} {boost:+d}: "
                            f"{', '.join(align_reasons)}]. {reasoning}"
                        )
                    if align_score < 0.3 and conviction < 75:
                        verdict = "WATCH"
                        reasoning = (
                            f"Active strategy misaligned (score={align_score:.2f}). "
                            f"Downgraded to WATCH. {reasoning[:200]}"
                        )
            except Exception:
                pass

        # ── Institutional threshold enforcement ─────────────────────────
        # If entropy/Hurst raised the threshold, a trade must meet the higher bar
        _shadow_side: str | None = None
        _effective_threshold = _ACTIONABLE_CONVICTION + _inst_conv_adjustment
        if verdict in ("BUY", "SELL") and conviction < _effective_threshold:
            original_verdict = verdict
            _shadow_side = original_verdict
            verdict = "WATCH"
            reasoning = (
                f"Conviction {conviction} below effective threshold {_effective_threshold} "
                f"(base={_ACTIONABLE_CONVICTION} + institutional adj={_inst_conv_adjustment:+d}) "
                f"for {original_verdict}. Downgraded to WATCH. {reasoning[:250]}"
            )
        elif verdict in ("BUY", "SELL") and conviction < _ACTIONABLE_CONVICTION:
            original_verdict = verdict
            _shadow_side = original_verdict
            verdict = "WATCH"
            reasoning = (
                f"Conviction {conviction} below minimum {_ACTIONABLE_CONVICTION} for {original_verdict}. "
                f"Downgraded to WATCH. {reasoning[:300]}"
            )
        elif verdict == "NO_TRADE" and bull_conv >= 60 and bull_conv >= bear_conv:
            _shadow_side = "BUY"
        elif verdict == "NO_TRADE" and bear_conv >= 60 and bear_conv > bull_conv:
            _shadow_side = "SELL"

        if _shadow_side and self.redis:
            try:
                from ..shadow_trades import record_shadow_trade
                _entry_price = float((state.get("features") or {}).get("close", 0) or 0)
                await record_shadow_trade(
                    self.redis,
                    symbol=symbol,
                    side=_shadow_side,
                    entry_price=_entry_price,
                    conviction=int(conviction if _shadow_side == verdict else max(bull_conv, bear_conv)),
                    reason=reasoning,
                    session=str(state.get("session", "")),
                    verdict=verdict,
                )
            except Exception:
                logger.debug("arbitrator.shadow_trade_record_failed", exc_info=True)

        state["verdict"] = verdict
        state["verdict_conviction"] = conviction
        state["verdict_reasoning"] = reasoning
        state["messages"].append({
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "type": "verdict",
            "verdict": verdict,
            "conviction": conviction,
            "content": reasoning[:500],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        await self._publish_message(
            f"VERDICT: {verdict} | Conviction: {conviction}/100 | HTF: {htf_bias}({htf_alignment})",
            metadata={
                "verdict": verdict,
                "conviction": conviction,
                "bull_conviction": bull_conv,
                "bear_conviction": bear_conv,
                "htf_bias": htf_bias,
                "htf_alignment": htf_alignment,
            },
            state=state,
        )
        await self._log_reasoning(reasoning, state=state)

        return state

    def _parse_verdict(self, text: str) -> tuple[str, int, str]:
        verdict_match = re.search(r"VERDICT:\s*(BUY|SELL|NO_TRADE|WATCH)", text, re.IGNORECASE)
        verdict = verdict_match.group(1).upper() if verdict_match else "NO_TRADE"

        conv_match = re.search(r"CONVICTION:\s*(\d+)", text, re.IGNORECASE)
        conviction = int(conv_match.group(1)) if conv_match else 50

        reasoning_match = re.search(r"REASONING:\s*(.+)", text, re.IGNORECASE | re.DOTALL)
        reasoning = reasoning_match.group(1).strip() if reasoning_match else text

        return verdict, min(100, max(0, conviction)), reasoning

    def _heuristic_verdict(self, state: AgentState) -> tuple[str, int, str]:
        bull_conv = state.get("bull_conviction", 0)
        bear_conv = state.get("bear_conviction", 0)
        intermarket = state.get("intermarket_score", 0.0)
        flow = state.get("flow_score", 0.0)
        ml_signal = state.get("ml_signal", {})
        ml_dir = ml_signal.get("direction", "NO_TRADE")
        ml_conf = float(ml_signal.get("confidence", 0))

        # Regime-aware conflict resolution
        regime = state.get("regime_analysis", {})
        regime_type = regime.get("regime", "UNKNOWN")
        range_info = regime.get("range_info", {})

        if bull_conv >= 65 and bear_conv >= 65:
            # In ranging markets, use mean-reversion logic instead of blocking
            if regime_type == "RANGING" and range_info:
                mrs = range_info.get("mean_reversion_setup", "WAIT")
                pos = range_info.get("position_in_range", 0.5)
                if mrs == "BUY" and pos < 0.25:
                    return "BUY", 65, f"Heuristic RANGE: mean-reversion BUY at range support (pos={pos:.0%})"
                elif mrs == "SELL" and pos > 0.75:
                    return "SELL", 65, f"Heuristic RANGE: mean-reversion SELL at range resistance (pos={pos:.0%})"
            return "NO_TRADE", 50, "Both sides have high conviction — conflicting signals."

        # Boost conviction based on ML signal alignment
        ml_boost_bull = int(ml_conf * 15) if ml_dir == "BUY" else 0
        ml_boost_bear = int(ml_conf * 15) if ml_dir == "SELL" else 0

        # HTF alignment boost
        htf_alignment = state.get("htf_context", {}).get("alignment_score", 0)
        htf_boost_bull = max(0, htf_alignment * 3)   # +12 max if strongly bullish
        htf_boost_bear = max(0, -htf_alignment * 3)  # +12 max if strongly bearish

        combined_bull = bull_conv + (intermarket * 2) + (flow * 2) + ml_boost_bull + htf_boost_bull
        combined_bear = bear_conv + (-intermarket * 2) + (-flow * 2) + ml_boost_bear + htf_boost_bear

        if combined_bull > combined_bear and bull_conv >= 55:
            conviction = min(100, int((combined_bull / 100) * 100))
            return "BUY", conviction, f"Heuristic: bull signals dominate (bull={bull_conv}, im={intermarket}, flow={flow}, ml={ml_dir} {ml_conf:.2f}, htf={htf_alignment}, regime={regime_type})"
        elif combined_bear > combined_bull and bear_conv >= 55:
            conviction = min(100, int((combined_bear / 100) * 100))
            return "SELL", conviction, f"Heuristic: bear signals dominate (bear={bear_conv}, im={intermarket}, flow={flow}, ml={ml_dir} {ml_conf:.2f}, htf={htf_alignment}, regime={regime_type})"
        else:
            # If ML signal is strong enough on its own, use it
            if ml_conf >= 0.70:
                conv = min(100, int(ml_conf * 80))
                return ml_dir, conv, f"Heuristic: ML signal strong ({ml_dir} conf={ml_conf:.2f}) despite weak debate conviction."
            # In ranging markets with clear boundary, suggest the range trade
            if regime_type == "RANGING" and range_info:
                mrs = range_info.get("mean_reversion_setup", "WAIT")
                if mrs in ("BUY", "SELL"):
                    return mrs, 55, f"Heuristic: RANGE mean-reversion {mrs} at boundary (no strong debate edge)."
            return "WATCH", 40, "Heuristic: no clear directional edge."
