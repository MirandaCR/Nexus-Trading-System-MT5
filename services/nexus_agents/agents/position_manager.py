"""
A10 — El Gestor: Position Manager agent.

Monitors open positions and manages SL/TP, trailing, partial closes,
time-based exits (min/max hold), and session changes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
import yaml

from shared.asset_specs import get_asset_spec, get_pip_size, round_price
from shared.redis_client import RedisClient
from shared.runtime_settings import get_cached_settings

from ..state import AgentState
from ..tools.execution_tools import close_position, modify_order
from ..tools.market_tools import get_indicators
from ..tools.risk_tools import check_exposure
from .base_agent import BaseAgent

logger = structlog.get_logger(__name__)

RISK_RULES_PATH = Path(__file__).resolve().parents[3] / "config" / "risk_rules.yaml"


def _load_rules() -> dict[str, Any]:
    if RISK_RULES_PATH.exists():
        with open(RISK_RULES_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


_RULES = _load_rules()


class PositionManagerAgent(BaseAgent):
    def __init__(self, *, redis_client: RedisClient | None = None) -> None:
        super().__init__("A10", "Position Manager", redis_client=redis_client)

    async def invoke(self, state: AgentState) -> AgentState:
        symbol = state["symbol"]

        try:
            exposure = await check_exposure()
        except Exception:
            exposure = {}

        positions = exposure.get("positions", [])
        symbol_positions = [p for p in positions if p.get("symbol") == symbol]

        if not symbol_positions:
            state["messages"].append({
                "agent_id": self.agent_id,
                "agent_name": self.name,
                "type": "position_management",
                "content": f"No open positions for {symbol}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return state

        timeframe = state.get("timeframe", "M5")
        try:
            indicators = await get_indicators(symbol, timeframe)
        except Exception:
            indicators = {}

        current_price = indicators.get("close", 0.0)
        atr = indicators.get("atr_14", indicators.get("atr", get_asset_spec(symbol).min_atr))
        spec = get_asset_spec(symbol)
        pip_size = get_pip_size(symbol)

        runtime = get_cached_settings()
        min_hold = int(runtime.get(
            "min_hold_minutes",
            _RULES.get("time_exit", {}).get("min_hold_minutes", 5),
        ))
        max_duration = int(runtime.get(
            "max_hold_minutes",
            _RULES.get("time_exit", {}).get("max_duration_minutes", 60),
        ))
        trailing_activation = _RULES.get("trailing_stop", {}).get("activation", "after_tp1")
        trail_atr_mult = _RULES.get("trailing_stop", {}).get("trail_atr_multiplier", 0.5)

        management_actions: list[dict] = []

        for pos in symbol_positions:
            pos_id = pos.get("id", "unknown")
            side = pos.get("side", "BUY")
            entry = pos.get("entry_price", 0.0)
            current_sl = pos.get("sl", 0.0)
            tp1 = pos.get("tp1", 0.0)
            tp2 = pos.get("tp2", 0.0)
            opened_at = pos.get("opened_at", "")

            duration_minutes = 0.0
            if opened_at:
                try:
                    open_dt = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
                    duration_minutes = (datetime.now(timezone.utc) - open_dt).total_seconds() / 60
                except Exception:
                    pass

            below_min_hold = duration_minutes < min_hold

            if duration_minutes >= max_duration:
                management_actions.append({
                    "position_id": pos_id,
                    "action": "time_exit",
                    "reason": f"Max hold reached ({duration_minutes:.0f} >= {max_duration} min)",
                })
                try:
                    await close_position(pos_id)
                except Exception as exc:
                    state["errors"].append(f"A10: time_exit close failed: {exc}")
                continue

            if below_min_hold:
                management_actions.append({
                    "position_id": pos_id,
                    "action": "hold_min_period",
                    "reason": f"Min hold {min_hold} min not reached ({duration_minutes:.1f} min)",
                })
                continue

            tp1_hit = (side == "BUY" and current_price >= tp1 > 0) or (side == "SELL" and current_price <= tp1 > 0)
            tp2_hit = (side == "BUY" and current_price >= tp2 > 0) or (side == "SELL" and current_price <= tp2 > 0)

            if tp1_hit and not pos.get("tp1_closed"):
                management_actions.append({
                    "position_id": pos_id,
                    "action": "partial_close_tp1",
                    "reason": f"TP1 hit at {current_price}",
                })

                if trailing_activation == "after_tp1":
                    if side == "BUY":
                        trail_sl = current_price - (atr * trail_atr_mult)
                        new_sl = max(entry, trail_sl)
                    else:
                        trail_sl = current_price + (atr * trail_atr_mult)
                        new_sl = min(entry, trail_sl)
                    try:
                        await modify_order(pos_id, {"sl": round_price(symbol, new_sl)})
                        management_actions.append({
                            "position_id": pos_id,
                            "action": "trailing_activated",
                            "new_sl": new_sl,
                        })
                    except Exception as exc:
                        state["errors"].append(f"A10: modify_order failed: {exc}")

            if tp2_hit and not pos.get("tp2_closed"):
                management_actions.append({
                    "position_id": pos_id,
                    "action": "partial_close_tp2",
                    "reason": f"TP2 hit at {current_price}",
                })

            if pos.get("trailing_active"):
                if side == "BUY":
                    new_trail = current_price - (atr * trail_atr_mult)
                    if new_trail > current_sl:
                        try:
                            await modify_order(pos_id, {"sl": round_price(symbol, new_trail)})
                            management_actions.append({
                                "position_id": pos_id,
                                "action": "trailing_updated",
                                "new_sl": new_trail,
                            })
                        except Exception as exc:
                            state["errors"].append(f"A10: trail update failed: {exc}")
                else:
                    new_trail = current_price + (atr * trail_atr_mult)
                    if new_trail < current_sl or current_sl == 0:
                        try:
                            await modify_order(pos_id, {"sl": round_price(symbol, new_trail)})
                            management_actions.append({
                                "position_id": pos_id,
                                "action": "trailing_updated",
                                "new_sl": new_trail,
                            })
                        except Exception as exc:
                            state["errors"].append(f"A10: trail update failed: {exc}")

            news = state.get("news_assessment", "")
            if "AVOID" in news.upper() or "HEDGE" in news.upper():
                management_actions.append({
                    "position_id": pos_id,
                    "action": "news_alert",
                    "reason": "News suggests AVOID/HEDGE",
                })

            session = state.get("session", "")
            if session.upper() == "OFF_HOURS" and not below_min_hold:
                management_actions.append({
                    "position_id": pos_id,
                    "action": "session_exit",
                    "reason": "Session OFF_HOURS",
                })
                try:
                    await close_position(pos_id)
                except Exception as exc:
                    state["errors"].append(f"A10: session exit failed: {exc}")

        summary = f"Managed {len(symbol_positions)} position(s) for {symbol}. Actions: {len(management_actions)}"
        state["messages"].append({
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "type": "position_management",
            "content": summary,
            "actions": management_actions,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        await self._publish_message(summary, metadata={
            "actions": management_actions,
            "positions_managed": len(symbol_positions),
        }, state=state)

        return state
