"""
A1 — El Técnico: Market Analyst agent.

Performs multi-timeframe technical analysis across M1-M15 with H1-H4 context.
Identifies trend structure, divergences, chart patterns, S/R levels (advanced
DBSCAN + order blocks), and indicator confluences.  Regime-aware analysis
framing adapts the prompt context based on A0's regime classification.
Returns a structured technical_analysis string.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog

from shared.redis_client import RedisClient

from ..state import AgentState
from ..tools.analysis_tools import calculate_sr, detect_patterns
from ..tools.market_tools import get_indicators, query_duckdb
from .base_agent import BaseAgent

logger = structlog.get_logger(__name__)


class MarketAnalystAgent(BaseAgent):
    def __init__(self, *, redis_client: RedisClient | None = None) -> None:
        super().__init__("A1", "Market Analyst", redis_client=redis_client)

    async def invoke(self, state: AgentState) -> AgentState:
        symbol = state["symbol"]
        timeframe = state.get("timeframe", "M15")

        indicators: dict = {}
        sr_levels: dict = {}
        patterns: list = []
        ohlcv_summary: str = ""
        advanced_levels: dict = {}
        divergences: list = []

        try:
            indicators = await get_indicators(symbol, timeframe)
        except Exception as exc:
            state["errors"].append(f"A1: get_indicators failed: {exc}")

        try:
            ohlcv_rows = await query_duckdb(
                f"SELECT * FROM ohlcv WHERE symbol='{symbol}' "
                f"AND timeframe='{timeframe}' ORDER BY timestamp DESC LIMIT 100"
            )
            if ohlcv_rows:
                ohlcv_summary = f"Retrieved {len(ohlcv_rows)} candles for {symbol} {timeframe}"
                sr_levels = await calculate_sr(ohlcv_rows)
                patterns = await detect_patterns(ohlcv_rows)
                divergences = self._detect_divergences(ohlcv_rows, indicators)
        except Exception as exc:
            state["errors"].append(f"A1: OHLCV/SR/pattern analysis failed: {exc}")

        # Fetch advanced DBSCAN S/R + order blocks from features service
        try:
            advanced_levels = await self._get_advanced_levels(symbol, timeframe)
        except Exception as exc:
            state["errors"].append(f"A1: advanced levels failed: {exc}")

        # Use pre-fetched multi-timeframe context from pipeline
        htf_ctx = state.get("htf_context", {})
        htf_bias = htf_ctx.get("bias", "NEUTRAL")
        htf_alignment = htf_ctx.get("alignment_score", 0)
        htf_details = htf_ctx.get("htf_details", {})
        htf_list = htf_ctx.get("htf_list", [])

        htf_summary = f"MTF Bias: {htf_bias} (alignment={htf_alignment}/4, TFs={htf_list})"
        for tf, detail in htf_details.items():
            htf_summary += (
                f"\n  {tf}: RSI={detail.get('rsi', 'N/A')}, "
                f"EMA20={detail.get('ema_20', 'N/A')}, "
                f"EMA50={detail.get('ema_50', 'N/A')}, "
                f"EMA200={detail.get('ema_200', 'N/A')}, "
                f"MACD_hist={detail.get('macd_histogram', 'N/A')}, "
                f"trend={detail.get('trend_direction', 'N/A')}"
            )

        # ── Regime-aware context framing ──
        regime = state.get("regime_analysis", {})
        regime_type = regime.get("regime", "UNKNOWN")
        regime_block = self._build_regime_context(regime)

        # ── Build enhanced context block ──
        context_block = (
            f"Symbol: {symbol}\n"
            f"Timeframe: {timeframe}\n\n"
            f"=== MARKET REGIME (from A0) ===\n{regime_block}\n\n"
            f"=== INDICATORS ===\n{json.dumps(indicators, default=str)}\n\n"
            f"=== SUPPORT/RESISTANCE (Basic) ===\n{json.dumps(sr_levels, default=str)}\n\n"
        )

        if advanced_levels:
            context_block += (
                f"=== ADVANCED S/R (DBSCAN Clustered) ===\n"
                f"{json.dumps(advanced_levels.get('support_resistance', []), default=str)}\n\n"
                f"=== ORDER BLOCKS ===\n"
                f"{json.dumps(advanced_levels.get('order_blocks', []), default=str)}\n\n"
                f"=== ROUND NUMBERS ===\n"
                f"{json.dumps(advanced_levels.get('round_numbers', []), default=str)}\n\n"
            )

        if divergences:
            context_block += (
                f"=== DIVERGENCES DETECTED ===\n"
                f"{json.dumps(divergences, default=str)}\n\n"
            )

        # BB Squeeze detection
        bb_squeeze = self._detect_bb_squeeze(indicators, state.get("features", {}))
        if bb_squeeze:
            context_block += f"=== BOLLINGER BAND SQUEEZE ===\n{bb_squeeze}\n\n"

        context_block += (
            f"=== PATTERNS ===\n{json.dumps(patterns, default=str)}\n\n"
            f"=== HIGHER-TIMEFRAME CONTEXT ===\n{htf_summary}\n\n"
            f"OHLCV: {ohlcv_summary}\n"
            f"ML Signal: {json.dumps(state.get('ml_signal', {}), default=str)}\n\n"
        )

        # Regime-specific instructions
        if regime_type == "RANGING":
            context_block += (
                f"\n** REGIME-SPECIFIC INSTRUCTIONS (RANGING) **\n"
                f"Focus on: mean-reversion setups at S/R boundaries, "
                f"BB band extremes, RSI overbought/oversold reversals.\n"
                f"Avoid: trend-following entries. "
                f"Look for: range boundaries, fade moves at extremes.\n"
            )
        elif regime_type == "BREAKOUT":
            context_block += (
                f"\n** REGIME-SPECIFIC INSTRUCTIONS (BREAKOUT) **\n"
                f"Focus on: BB squeeze breakout direction, volume confirmation, "
                f"false breakout risk assessment.\n"
                f"Key: wait for close outside range + volume surge.\n"
            )
        else:
            context_block += (
                f"\n** REGIME-SPECIFIC INSTRUCTIONS (TRENDING) **\n"
                f"Focus on: pullback entries in trend direction, "
                f"EMA support/resistance, trend continuation patterns.\n"
                f"If MTF alignment is strong ({htf_alignment}/4), "
                f"trades AGAINST the higher-timeframe bias require extreme caution.\n"
            )

        try:
            llm = self._get_llm()
            prompt = self._load_prompt("market_analyst")
            chain = prompt | llm
            response = await self._invoke_with_budget(chain, {"input": context_block})
            analysis = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            analysis = (
                f"[LLM unavailable — raw analysis]\n"
                f"Regime: {regime_type}\n"
                f"Indicators: {json.dumps(indicators, default=str)}\n"
                f"S/R: {json.dumps(sr_levels, default=str)}\n"
                f"Advanced S/R: {json.dumps(advanced_levels, default=str)}\n"
                f"Divergences: {json.dumps(divergences, default=str)}\n"
                f"Patterns: {json.dumps(patterns, default=str)}\n"
                f"MTF: {htf_summary}"
            )
            state["errors"].append(f"A1: LLM call failed: {exc}")

        state["technical_analysis"] = analysis
        state["messages"].append({
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "type": "analysis",
            "content": analysis[:500],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        await self._publish_message(analysis[:500], metadata={
            "indicators": indicators,
            "sr_levels": sr_levels,
            "advanced_levels": advanced_levels,
            "divergences": divergences,
            "patterns": patterns,
            "regime": regime_type,
        }, state=state)
        await self._log_reasoning(analysis, state=state)

        return state

    # ── Advanced analysis helpers ─────────────────────────────────────────

    def _detect_divergences(
        self,
        ohlcv_rows: list[dict],
        indicators: dict,
    ) -> list[dict[str, Any]]:
        """Detect RSI and MACD divergences from recent price action."""
        divergences: list[dict[str, Any]] = []
        if len(ohlcv_rows) < 10:
            return divergences

        # Get recent price highs/lows
        closes = [float(r.get("close", 0)) for r in ohlcv_rows[:20]]
        highs = [float(r.get("high", 0)) for r in ohlcv_rows[:20]]
        lows = [float(r.get("low", 0)) for r in ohlcv_rows[:20]]

        if not closes or len(closes) < 5:
            return divergences

        rsi = float(indicators.get("rsi", indicators.get("rsi_14", 50)))
        macd_hist = float(indicators.get("macd_histogram", indicators.get("macd_hist", 0)))

        # Simple divergence: price making new highs but RSI lower
        recent_high = max(highs[:5])
        prior_high = max(highs[5:15]) if len(highs) > 5 else recent_high
        recent_low = min(lows[:5])
        prior_low = min(lows[5:15]) if len(lows) > 5 else recent_low

        # Bearish divergence: price higher high, RSI lower
        if recent_high > prior_high and rsi < 60:
            divergences.append({
                "type": "bearish_divergence",
                "indicator": "RSI",
                "description": f"Price made higher high ({recent_high:.5f}) but RSI is declining ({rsi:.1f})",
                "significance": "high" if rsi < 50 else "medium",
            })

        # Bullish divergence: price lower low, RSI higher
        if recent_low < prior_low and rsi > 40:
            divergences.append({
                "type": "bullish_divergence",
                "indicator": "RSI",
                "description": f"Price made lower low ({recent_low:.5f}) but RSI is rising ({rsi:.1f})",
                "significance": "high" if rsi > 50 else "medium",
            })

        # MACD histogram divergence
        if recent_high > prior_high and macd_hist < 0:
            divergences.append({
                "type": "bearish_divergence",
                "indicator": "MACD",
                "description": f"Price higher high but MACD histogram negative ({macd_hist:.4f})",
                "significance": "medium",
            })

        if recent_low < prior_low and macd_hist > 0:
            divergences.append({
                "type": "bullish_divergence",
                "indicator": "MACD",
                "description": f"Price lower low but MACD histogram positive ({macd_hist:.4f})",
                "significance": "medium",
            })

        return divergences

    def _detect_bb_squeeze(self, indicators: dict, features: dict) -> str:
        """Detect Bollinger Band squeeze condition."""
        bb_upper = float(features.get("bb_upper", indicators.get("bb_upper", 0)))
        bb_lower = float(features.get("bb_lower", indicators.get("bb_lower", 0)))
        bb_middle = float(features.get("bb_middle", indicators.get("bb_mid", 0)))
        close = float(features.get("close", indicators.get("close", 0)))

        if bb_upper <= 0 or bb_lower <= 0:
            return ""

        bb_width = (bb_upper - bb_lower) / bb_middle if bb_middle > 0 else 0
        if bb_width < 0.005:
            position = (close - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5
            return (
                f"BB SQUEEZE ACTIVE: Width={bb_width:.4f} (very tight). "
                f"Price position in bands: {position:.1%}. "
                f"Potential breakout imminent — watch for volume surge."
            )
        elif bb_width < 0.01:
            return (
                f"BB NARROWING: Width={bb_width:.4f}. "
                f"Compression building — breakout setup forming."
            )
        return ""

    def _build_regime_context(self, regime: dict) -> str:
        """Build a human-readable regime context block."""
        if not regime:
            return "Regime: UNKNOWN (A0 not available)"

        regime_type = regime.get("regime", "UNKNOWN")
        strength = regime.get("regime_strength", 0)
        approach = regime.get("suggested_approach", "")
        vol = regime.get("vol_regime", "MEDIUM")

        text = (
            f"Regime: {regime_type} (strength={strength}/100)\n"
            f"Volatility: {vol}\n"
            f"Suggested approach: {approach}"
        )

        range_info = regime.get("range_info", {})
        if range_info:
            text += (
                f"\nRange boundaries: "
                f"{range_info.get('range_low', 0):.5f} — "
                f"{range_info.get('range_high', 0):.5f} "
                f"(mid={range_info.get('range_mid', 0):.5f})"
                f"\nPosition in range: {range_info.get('position_in_range', 0):.1%}"
                f"\nMean-reversion setup: {range_info.get('mean_reversion_setup', 'NONE')}"
            )

        breakout = regime.get("breakout_signals", {})
        if breakout:
            text += (
                f"\nBreakout probability: {breakout.get('breakout_probability', 0):.0%}"
                f"\nSqueeze: {'YES' if breakout.get('squeeze_active') else 'NO'}"
            )

        return text

    async def _get_advanced_levels(self, symbol: str, timeframe: str) -> dict[str, Any]:
        """Fetch DBSCAN-clustered S/R, order blocks, and round numbers from features service."""
        import httpx
        import os

        features_url = os.getenv("NEXUS_FEATURES_URL", "http://localhost:8002")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{features_url}/api/features/calculate",
                    json={
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "ohlcv": [],  # empty triggers cache-only
                        "include_levels": True,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("levels", {})
        except Exception:
            pass

        # Fallback: try reading from Redis cache
        if self.redis:
            try:
                cache_key = f"nexus:features:{symbol}:{timeframe}"
                raw = await self.redis.cache_get(cache_key)
                if raw and isinstance(raw, dict):
                    return raw.get("levels", {})
            except Exception:
                pass
        return {}
