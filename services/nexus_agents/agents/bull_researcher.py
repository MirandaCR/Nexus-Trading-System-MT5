"""
A5 — El Optimista: Bull Researcher agent.

Builds the strongest possible bullish case from all available analysis
(technical, news, intermarket, order flow).  Outputs a structured bull_case
text and a bull_conviction score (0-100).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import structlog

from shared.redis_client import RedisClient

from ..state import AgentState
from .base_agent import BaseAgent

logger = structlog.get_logger(__name__)


class BullResearcherAgent(BaseAgent):
    def __init__(self, *, redis_client: RedisClient | None = None) -> None:
        super().__init__("A5", "Bull Researcher", redis_client=redis_client)

    async def invoke(self, state: AgentState) -> AgentState:
        symbol = state["symbol"]

        htf_ctx = state.get("htf_context", {})
        htf_bias = htf_ctx.get("bias", "NEUTRAL")
        htf_alignment = htf_ctx.get("alignment_score", 0)

        context_block = (
            f"Symbol: {symbol}\n"
            f"Session: {state.get('session', 'UNKNOWN')}\n"
            f"ML Signal: {json.dumps(state.get('ml_signal', {}), default=str)}\n\n"
            f"=== MULTI-TIMEFRAME CONTEXT ===\n"
            f"HTF Bias: {htf_bias} (alignment score: {htf_alignment}/4)\n"
            f"HTF Details: {json.dumps(htf_ctx.get('htf_details', {}), default=str)}\n\n"
            f"=== TECHNICAL ANALYSIS (A1) ===\n"
            f"{state.get('technical_analysis', 'Not available')}\n\n"
            f"=== NEWS ASSESSMENT (A2) ===\n"
            f"{state.get('news_assessment', 'Not available')}\n\n"
            f"=== INTERMARKET SCORE (A3) ===\n"
            f"Score: {state.get('intermarket_score', 0.0)}/10\n\n"
            f"=== ORDER FLOW SCORE (A4) ===\n"
            f"Score: {state.get('flow_score', 0.0)}/10\n\n"
            f"=== FEATURES ===\n"
            f"{json.dumps(state.get('features', {}), default=str)}\n\n"
            f"Build the STRONGEST possible BULLISH case for {symbol}. "
            f"You MUST argue for going LONG. Find every piece of evidence that "
            f"supports a bullish thesis. Consider the higher-timeframe context — "
            f"if HTF is BULLISH, that strengthens the bull case significantly. "
            f"If HTF is BEARISH, acknowledge it as a headwind. At the end, provide:\n"
            f"CONVICTION: <0-100>\n"
            f"where 0 = no bullish case, 100 = extreme confidence in upside."
        )

        try:
            llm = self._get_llm()
            prompt = self._load_prompt("bull_researcher")
            chain = prompt | llm
            response = await self._invoke_with_budget(chain, {"input": context_block})
            raw_output = response.content if hasattr(response, "content") else str(response)
            conviction = self._extract_conviction(raw_output)
            bull_case = raw_output
        except Exception as exc:
            bull_case = self._build_heuristic_case(state)
            conviction = self._heuristic_conviction(state)
            state["errors"].append(f"A5: LLM call failed: {exc}")

        conviction = max(0, min(100, conviction))
        state["bull_case"] = bull_case
        state["bull_conviction"] = conviction
        state["messages"].append({
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "type": "debate",
            "side": "bull",
            "content": bull_case[:500],
            "conviction": conviction,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        await self._publish_message(f"Bull conviction: {conviction}/100", metadata={
            "conviction": conviction,
            "case_preview": bull_case[:200],
        }, state=state)
        await self._log_reasoning(bull_case, state=state)

        return state

    def _extract_conviction(self, text: str) -> int:
        # Try multiple patterns in order of specificity
        for pattern in [
            r"CONVICTION:\s*(\d+)",                    # CONVICTION: 72
            r"\*\*CONVICTION:?\*\*\s*(\d+)",           # **CONVICTION:** 72
            r"conviction\s*(?:score)?:?\s*(\d+)",      # conviction score: 72
            r"(\d+)\s*/\s*100",                        # 72/100
            r"conviction\s+(?:of|is|at)\s+(\d+)",      # conviction of 72
        ]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                val = int(match.group(1))
                if 0 <= val <= 100:
                    return val
        return 50

    def _build_heuristic_case(self, state: AgentState) -> str:
        points = []
        if state.get("intermarket_score", 0) > 0:
            points.append(f"Intermarket alignment is positive ({state['intermarket_score']}/10)")
        if state.get("flow_score", 0) > 0:
            points.append(f"Order flow shows buying pressure ({state['flow_score']}/10)")
        ml = state.get("ml_signal", {})
        if ml.get("direction") == "BUY":
            points.append(f"ML model predicts upside (confidence: {ml.get('confidence', 'N/A')})")
        if state.get("technical_analysis"):
            points.append("Technical analysis available for review")
        return "BULLISH CASE:\n" + "\n".join(f"- {p}" for p in points) if points else "Limited bullish evidence found."

    def _heuristic_conviction(self, state: AgentState) -> int:
        score = 40  # Start slightly below neutral
        features = state.get("features", {})

        # Technical indicators
        rsi = float(features.get("rsi", 50))
        if rsi > 50:
            score += min(15, int((rsi - 50) * 0.3))  # RSI above 50 = bullish
        macd_hist = float(features.get("macd_histogram", 0))
        if macd_hist > 0:
            score += 10
        # EMA alignment: price above EMA 50
        close = float(features.get("close", 0))
        ema50 = float(features.get("ema_50", 0))
        if close > 0 and ema50 > 0 and close > ema50:
            score += 10
        # ADX trend strength
        adx = float(features.get("adx", 0))
        di_plus = float(features.get("di_plus", 0))
        di_minus = float(features.get("di_minus", 0))
        if adx > 25 and di_plus > di_minus:
            score += 10

        # High-level scores
        if state.get("intermarket_score", 0) > 3:
            score += 8
        if state.get("flow_score", 0) > 3:
            score += 8

        # ML signal
        ml = state.get("ml_signal", {})
        if ml.get("direction") == "BUY":
            score += int(float(ml.get("confidence", 0)) * 20)

        # HTF alignment: bullish HTF boosts bull conviction, bearish penalises
        htf_alignment = state.get("htf_context", {}).get("alignment_score", 0)
        score += htf_alignment * 3  # +12 at max bull, -12 at max bear

        return max(0, min(100, score))
