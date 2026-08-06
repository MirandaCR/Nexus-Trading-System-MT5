"""
A4 — El Microestructurero: Order Flow Specialist agent.

Analyzes bid/ask volume, volume delta, absorption patterns, and spread
dynamics.  Returns a flow_score from -10 (strong sell pressure) to
+10 (strong buy pressure).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from shared.redis_client import RedisClient

from ..state import AgentState
from ..tools.analysis_tools import get_flow
from ..tools.market_tools import get_indicators
from .base_agent import BaseAgent

logger = structlog.get_logger(__name__)


class OrderFlowAgent(BaseAgent):
    def __init__(self, *, redis_client: RedisClient | None = None) -> None:
        super().__init__("A4", "Order Flow Specialist", redis_client=redis_client)

    async def invoke(self, state: AgentState) -> AgentState:
        symbol = state["symbol"]

        flow_data: dict = {}
        indicators: dict = {}

        try:
            flow_data = await get_flow(symbol)
        except Exception as exc:
            state["errors"].append(f"A4: get_flow failed: {exc}")

        try:
            indicators = await get_indicators(symbol, "M1")
        except Exception as exc:
            state["errors"].append(f"A4: get_indicators M1 failed: {exc}")

        bid_volume = flow_data.get("bid_volume", 0)
        ask_volume = flow_data.get("ask_volume", 0)

        # Fallback: derive pressure from M1 feature volume when flow API empty
        if bid_volume == 0 and ask_volume == 0:
            feat_vol = float(indicators.get("volume", 0) or state.get("features", {}).get("volume", 0))
            close = float(indicators.get("close", 0) or state.get("features", {}).get("close", 0))
            prev_close = float(indicators.get("prev_close", close) or state.get("features", {}).get("prev_close", close))
            if feat_vol > 0:
                if close >= prev_close:
                    bid_volume = feat_vol
                else:
                    ask_volume = feat_vol

        total_volume = bid_volume + ask_volume or 1
        delta = flow_data.get("delta", ask_volume - bid_volume)
        delta_pct = delta / total_volume * 100 if total_volume else 0

        absorption = flow_data.get("absorption", "none")
        spread_current = flow_data.get("spread", 0)
        spread_avg = flow_data.get("spread_avg", spread_current or 1)
        spread_ratio = spread_current / spread_avg if spread_avg else 1.0

        imbalance = flow_data.get("imbalance", 0)
        large_orders = flow_data.get("large_orders", [])
        iceberg_detected = flow_data.get("iceberg_detected", False)

        context_block = (
            f"Symbol: {symbol}\n"
            f"Bid volume: {bid_volume}, Ask volume: {ask_volume}\n"
            f"Volume delta: {delta} ({delta_pct:.1f}%)\n"
            f"Absorption: {absorption}\n"
            f"Spread: {spread_current} (avg: {spread_avg}, ratio: {spread_ratio:.2f})\n"
            f"Order imbalance: {imbalance}\n"
            f"Large orders: {json.dumps(large_orders[:10], default=str)}\n"
            f"Iceberg detected: {iceberg_detected}\n"
            f"M1 indicators: {json.dumps(indicators, default=str)}\n"
            f"Technical analysis: {state.get('technical_analysis', 'N/A')[:200]}"
        )

        try:
            llm = self._get_llm(fast=True)
            prompt = self._load_prompt("order_flow")
            chain = prompt | llm
            response = await self._invoke_with_budget(chain, {"input": context_block})
            raw_output = response.content if hasattr(response, "content") else str(response)
            score = self._extract_score(raw_output)
        except Exception as exc:
            score = self._heuristic_score(delta_pct, absorption, spread_ratio, imbalance)
            raw_output = f"[Heuristic flow score] {score}"
            state["errors"].append(f"A4: LLM call failed: {exc}")

        score = max(-10.0, min(10.0, score))
        state["flow_score"] = score
        state["messages"].append({
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "type": "analysis",
            "content": raw_output[:500],
            "score": score,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        await self._publish_message(f"Order flow score: {score:.1f}", metadata={
            "score": score,
            "delta_pct": delta_pct,
            "absorption": absorption,
            "spread_ratio": spread_ratio,
        }, state=state)
        await self._log_reasoning(raw_output, state=state)

        return state

    def _extract_score(self, text: str) -> float:
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
        delta_pct: float,
        absorption: str,
        spread_ratio: float,
        imbalance: float,
    ) -> float:
        score = 0.0

        if delta_pct > 20:
            score += 4.0
        elif delta_pct > 10:
            score += 2.0
        elif delta_pct < -20:
            score -= 4.0
        elif delta_pct < -10:
            score -= 2.0

        if absorption == "buy":
            score += 2.0
        elif absorption == "sell":
            score -= 2.0

        if spread_ratio > 2.0:
            score *= 0.5

        score += imbalance * 0.5

        return max(-10.0, min(10.0, score))
