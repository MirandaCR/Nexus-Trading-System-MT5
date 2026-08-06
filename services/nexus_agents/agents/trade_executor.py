"""
A9 — El Operador: Trade Executor agent.

Determines order type (market/limit), entry price, stop-loss, 3-tier
take-profit levels, and trailing stop parameters.  Sends the order to the
broker via the execution service.  Returns the order dict.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
import yaml

from shared.asset_specs import get_asset_spec, round_price
from shared.nexus_trades import build_nexus_comment
from shared.redis_client import RedisClient
from shared.sl_tp import compute_sltp

from ..state import AgentState
from ..tools.execution_tools import send_order
from ..tools.market_tools import get_indicators, get_price
from ..tools.risk_tools import record_trade
from .base_agent import BaseAgent

logger = structlog.get_logger(__name__)

RISK_RULES_PATH = Path(__file__).resolve().parents[3] / "config" / "risk_rules.yaml"


def _load_rules() -> dict[str, Any]:
    if RISK_RULES_PATH.exists():
        with open(RISK_RULES_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


_RULES = _load_rules()
_TP_STRUCTURE = _RULES.get("tp_structure", {})


class TradeExecutorAgent(BaseAgent):
    def __init__(self, *, redis_client: RedisClient | None = None) -> None:
        super().__init__("A9", "Trade Executor", redis_client=redis_client)

    @staticmethod
    def _build_order_comment(state: AgentState, verdict: str) -> str:
        regime = (state.get("regime_analysis") or {}).get("regime", "")
        strategy = (state.get("active_strategy") or regime or "genai").lower()
        tf = str(state.get("timeframe", "M5")).lower()
        return build_nexus_comment(strategy, tag=f"{tf}{verdict[:1].lower()}")

    async def invoke(self, state: AgentState) -> AgentState:
        symbol = state["symbol"]
        verdict = state.get("verdict", "NO_TRADE")
        risk_decision = state.get("risk_decision", "REJECT")
        position_size = state.get("position_size", 0.0)

        if verdict in ("NO_TRADE", "WATCH") or risk_decision == "REJECT" or position_size <= 0:
            state["order"] = None
            state["messages"].append({
                "agent_id": self.agent_id,
                "agent_name": self.name,
                "type": "execution",
                "content": f"No order: verdict={verdict}, risk={risk_decision}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return state

        timeframe = state.get("timeframe", "M5")
        try:
            indicators = await get_indicators(symbol, timeframe)
        except Exception:
            indicators = {}

        try:
            live_tick = await get_price(symbol)
        except Exception:
            live_tick = {}

        bid = live_tick.get("bid", 0.0) or indicators.get("bid", 0.0)
        ask = live_tick.get("ask", 0.0) or indicators.get("ask", 0.0)

        if bid == 0.0 or ask == 0.0:
            current_price = indicators.get("close", state.get("features", {}).get("close", 0.0))
            bid = bid or current_price
            ask = ask or current_price

        if bid == 0.0 and ask == 0.0:
            state["order"] = None
            state["errors"].append("A9: no live MT5 price available — refusing synthetic entry")
            return state

        atr = indicators.get("atr_14", indicators.get("atr", 0))
        avg_atr = indicators.get("atr_avg", indicators.get("atr_50", atr))
        spread = abs(ask - bid)
        session = state.get("session", "unknown")

        entry = ask if verdict == "BUY" else bid
        sltp = compute_sltp(
            symbol=symbol,
            side=verdict,
            entry=entry,
            atr=atr,
            avg_atr=avg_atr,
            session=session,
            rules=_RULES,
        )

        order_type = "market"
        if spread > atr * 0.3:
            order_type = "limit"
            entry = bid if verdict == "BUY" else ask

        spec = get_asset_spec(symbol)
        order = {
            "symbol": symbol,
            "side": verdict,
            "order_type": order_type,
            "entry": round_price(symbol, entry),
            "sl": sltp.sl,
            "tp1": sltp.tp1,
            "tp2": sltp.tp2,
            "tp3": sltp.tp3,
            "size": position_size,
            "trailing_stop": sltp.trailing_stop,
            "tp1_allocation": _TP_STRUCTURE.get("tp1", {}).get("allocation_pct", 0.5),
            "tp2_allocation": _TP_STRUCTURE.get("tp2", {}).get("allocation_pct", 0.3),
            "tp3_allocation": _TP_STRUCTURE.get("tp3", {}).get("allocation_pct", 0.2),
            "tp3_trailing": _TP_STRUCTURE.get("tp3", {}).get("trailing", True),
            "atr": round(atr, spec.digits),
            "atr_avg": round(avg_atr, spec.digits) if avg_atr else 0,
            "vol_regime": sltp.vol_regime,
            "vol_ratio": round(sltp.vol_ratio, 2),
            "session": session,
            "session_scale": sltp.session_scale,
            "conviction": state.get("verdict_conviction", 0) / 100.0,
            "cycle_id": state.get("cycle_id", ""),
            "bid": bid,
            "ask": ask,
            "current_spread": spread,
            "comment": self._build_order_comment(state, verdict),
            "strategy": (state.get("active_strategy") or (state.get("regime_analysis") or {}).get("regime") or "genai"),
            "regime": (state.get("regime_analysis") or {}).get("regime", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        context_for_llm = (
            f"Symbol: {symbol}\n"
            f"Verdict: {verdict}, Conviction: {state.get('verdict_conviction', 0)}\n"
            f"Current price: bid={bid}, ask={ask}, spread={spread}\n"
            f"ATR: {atr}, Session: {session}\n"
            f"SL dist: {sltp.sl_distance:.4f}, TP1: {sltp.tp1_distance:.4f}\n"
            f"Proposed order: {json.dumps(order, default=str)}\n"
            f"Validate this order and confirm or suggest adjustments."
        )

        try:
            llm = self._get_llm()
            prompt = self._load_prompt("trade_executor")
            chain = prompt | llm
            response = await self._invoke_with_budget(chain, {"input": context_for_llm})
            llm_review = response.content if hasattr(response, "content") else str(response)
        except Exception:
            llm_review = "LLM unavailable — using computed order params."

        try:
            result = await send_order(order)
            order["broker_response"] = result
            broker_status = str(result.get("status", "")).upper()
            order["status"] = (
                "sent"
                if broker_status in {"FILLED", "PARTIAL", "PENDING", "PLACED"}
                else "failed"
            )
            if order["status"] == "sent":
                await record_trade()
            elif order["status"] == "failed":
                state["errors"].append(
                    f"A9: broker rejected order: {result.get('error') or result}"
                )
        except Exception as exc:
            order["broker_response"] = {"error": str(exc)}
            order["status"] = "failed"
            state["errors"].append(f"A9: send_order failed: {exc}")

        state["order"] = order
        state["messages"].append({
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "type": "execution",
            "content": f"Order {order['status']}: {verdict} {position_size} {symbol} @ {entry}",
            "order": order,
            "llm_review": llm_review[:300],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        await self._publish_message(
            f"Order {order['status']}: {verdict} {position_size} lots @ {entry}",
            metadata=order,
            state=state,
        )
        await self._log_reasoning(
            f"Order: {json.dumps(order, default=str)}\nLLM: {llm_review[:300]}",
            state=state,
        )

        return state
