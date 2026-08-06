"""
A3 — El Macro: Intermarket Analyst agent.

Analyzes DXY, US10Y, VIX, Gold, Oil, SPX correlations.
Detects divergences between the target asset and correlated instruments.
Returns an intermarket_score from -10 (extremely bearish) to +10 (extremely bullish).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from shared.redis_client import RedisClient

from ..state import AgentState
from ..tools.market_tools import get_indicators, query_duckdb
from .base_agent import BaseAgent

logger = structlog.get_logger(__name__)

CORRELATED_INSTRUMENTS = ["DXY", "US10Y", "VIX", "XAUUSD", "USOIL", "US500"]

CORRELATION_MAP: dict[str, dict[str, float]] = {
    "EURUSD": {"DXY": -0.95, "US10Y": -0.4, "XAUUSD": 0.3, "US500": 0.2},
    "GBPUSD": {"DXY": -0.90, "US10Y": -0.35, "XAUUSD": 0.25, "US500": 0.15},
    "USDJPY": {"DXY": 0.85, "US10Y": 0.70, "VIX": -0.4, "US500": 0.3},
    "AUDUSD": {"DXY": -0.80, "XAUUSD": 0.45, "USOIL": 0.3, "US500": 0.35},
    "USDCAD": {"DXY": 0.75, "USOIL": -0.60, "US500": -0.15},
    "XAUUSD": {"DXY": -0.80, "US10Y": -0.50, "VIX": 0.40},
}


class IntermarketAgent(BaseAgent):
    def __init__(self, *, redis_client: RedisClient | None = None) -> None:
        super().__init__("A3", "Intermarket Analyst", redis_client=redis_client)

    async def invoke(self, state: AgentState) -> AgentState:
        symbol = state["symbol"]

        correlated_data: dict[str, dict] = {}
        for instr in CORRELATED_INSTRUMENTS:
            try:
                ind = await get_indicators(instr, "H1")
                if ind:
                    correlated_data[instr] = ind
            except Exception:
                pass

        divergences: list[str] = []
        expected_correlations = CORRELATION_MAP.get(symbol, {})
        for instr, expected_corr in expected_correlations.items():
            instr_data = correlated_data.get(instr, {})
            if not instr_data:
                continue
            instr_rsi = instr_data.get("rsi", 50)
            symbol_features = state.get("features", {})
            symbol_rsi = symbol_features.get("rsi", 50)

            if expected_corr > 0 and abs(instr_rsi - symbol_rsi) > 20:
                divergences.append(
                    f"Divergence with {instr} (expected +corr): "
                    f"{instr} RSI={instr_rsi:.1f}, {symbol} RSI={symbol_rsi:.1f}"
                )
            elif expected_corr < 0 and abs((100 - instr_rsi) - symbol_rsi) > 20:
                divergences.append(
                    f"Divergence with {instr} (expected -corr): "
                    f"{instr} RSI={instr_rsi:.1f}, {symbol} RSI={symbol_rsi:.1f}"
                )

        vix_data = correlated_data.get("VIX", {})
        vix_level = vix_data.get("close", vix_data.get("last", 20))
        risk_regime = "low_vol" if vix_level < 15 else ("high_vol" if vix_level > 25 else "normal")

        dxy_data = correlated_data.get("DXY", {})
        dxy_trend = "neutral"
        if dxy_data:
            ema20 = dxy_data.get("ema_20", 0)
            ema50 = dxy_data.get("ema_50", 0)
            if ema20 and ema50:
                dxy_trend = "bullish" if ema20 > ema50 else "bearish"

        context_block = (
            f"Symbol: {symbol}\n"
            f"Correlated instruments data: {json.dumps(correlated_data, default=str)}\n"
            f"Expected correlations: {json.dumps(expected_correlations, default=str)}\n"
            f"Detected divergences: {json.dumps(divergences, default=str)}\n"
            f"VIX level: {vix_level} ({risk_regime})\n"
            f"DXY trend: {dxy_trend}\n"
            f"Technical analysis: {state.get('technical_analysis', 'N/A')[:300]}\n"
            f"ML Signal: {json.dumps(state.get('ml_signal', {}), default=str)}"
        )

        try:
            llm = self._get_llm(fast=True)
            prompt = self._load_prompt("intermarket")
            chain = prompt | llm
            response = await self._invoke_with_budget(chain, {"input": context_block})
            raw_output = response.content if hasattr(response, "content") else str(response)

            score = self._extract_score(raw_output)
        except Exception as exc:
            score = self._heuristic_score(divergences, dxy_trend, risk_regime, symbol)
            raw_output = f"[Heuristic score] {score}"
            state["errors"].append(f"A3: LLM call failed: {exc}")

        score = max(-10.0, min(10.0, score))
        state["intermarket_score"] = score
        state["messages"].append({
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "type": "analysis",
            "content": raw_output[:500],
            "score": score,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        await self._publish_message(f"Intermarket score: {score:.1f}", metadata={
            "score": score,
            "divergences": divergences,
            "risk_regime": risk_regime,
            "dxy_trend": dxy_trend,
        }, state=state)
        await self._log_reasoning(raw_output, state=state)

        return state

    def _extract_score(self, text: str) -> float:
        """Try to parse a numeric score from LLM output."""
        import re
        match = re.search(r"SCORE:\s*([+-]?\d+\.?\d*)", text, re.IGNORECASE)
        if match:
            return float(match.group(1))
        match = re.search(r"([+-]?\d+\.?\d*)\s*/\s*10", text)
        if match:
            return float(match.group(1))
        numbers = re.findall(r"[+-]?\d+\.?\d*", text)
        for n in numbers:
            val = float(n)
            if -10 <= val <= 10:
                return val
        return 0.0

    def _heuristic_score(
        self,
        divergences: list[str],
        dxy_trend: str,
        risk_regime: str,
        symbol: str,
    ) -> float:
        score = 0.0
        score -= len(divergences) * 1.5

        is_usd_pair_long = symbol.startswith("USD") or symbol.endswith("USD")
        if dxy_trend == "bullish" and is_usd_pair_long:
            score += 2.0
        elif dxy_trend == "bearish" and is_usd_pair_long:
            score -= 2.0

        if risk_regime == "high_vol":
            score -= 2.0
        elif risk_regime == "low_vol":
            score += 1.0

        return max(-10.0, min(10.0, score))
