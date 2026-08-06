"""
A8 — El Guardián: Risk Manager agent.

Enforces ALL circuit breakers (INVIOLABLE), calculates position size using
dynamic Kelly sizing with Vol/Tilt/Session factors.

All limits scale dynamically with account equity:
  - Lot cap: equity / 5000
  - Daily trade limit: 3 + log2(equity / 100)
  - Cooldown between trades: configurable (default 120s)

Returns:
  - risk_decision: APPROVE / REJECT / MODIFY
  - position_size: lots
  - risk_reasoning: why
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
import yaml

from shared.constants import DEFAULT_RISK_PARAMS
from shared.redis_client import RedisClient

from ..state import AgentState
from ..tools.risk_tools import (
    _get_account_equity,
    calculate_size,
    check_correlation,
    check_exposure,
    dynamic_daily_limit,
    dynamic_lot_cap,
    get_open_positions,
    get_trade_count_today,
    record_trade,
    seconds_since_last_trade,
)
from .base_agent import BaseAgent

logger = structlog.get_logger(__name__)

RISK_RULES_PATH = Path(__file__).resolve().parents[3] / "config" / "risk_rules.yaml"


def _load_risk_rules() -> dict[str, Any]:
    if RISK_RULES_PATH.exists():
        with open(RISK_RULES_PATH) as f:
            return yaml.safe_load(f)
    return {}


_RISK_RULES = _load_risk_rules()
_AGGRESSIVE_TRADING = os.getenv("NEXUS_AGGRESSIVE_TRADING", "false").lower() == "true"
_ALLOW_OFF_HOURS = os.getenv("NEXUS_ALLOW_OFF_HOURS", "false").lower() == "true"  # aligned with graph.py


class RiskManagerAgent(BaseAgent):
    def __init__(self, *, redis_client: RedisClient | None = None) -> None:
        super().__init__("A8", "Risk Manager", redis_client=redis_client)
        self.rules = _RISK_RULES

    async def invoke(self, state: AgentState) -> AgentState:
        symbol = state["symbol"]
        verdict = state.get("verdict", "NO_TRADE")
        conviction = state.get("verdict_conviction", 0)

        if verdict in ("NO_TRADE", "WATCH"):
            state["risk_decision"] = "REJECT"
            state["position_size"] = 0.0
            state["risk_reasoning"] = f"No actionable verdict ({verdict}), skipping risk check."
            return state

        equity = await _get_account_equity()

        breaker_results = await self._check_circuit_breakers(state, equity)
        triggered = [b for b in breaker_results if b["triggered"]]

        if triggered:
            if _AGGRESSIVE_TRADING:
                soft_blockers = {"off_hours", "friday_close", "anti_tilt"}
                triggered = [b for b in triggered if b.get("name") not in soft_blockers]
        if triggered:
            reasons = "; ".join(b["reason"] for b in triggered)
            state["risk_decision"] = "REJECT"
            state["position_size"] = 0.0
            state["risk_reasoning"] = f"CIRCUIT BREAKER(S) TRIGGERED: {reasons}"
            state["messages"].append({
                "agent_id": self.agent_id,
                "agent_name": self.name,
                "type": "risk",
                "decision": "REJECT",
                "content": state["risk_reasoning"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await self._publish_message(state["risk_reasoning"], state=state)
            return state

        entry = state.get("features", {}).get("close", 0.0)
        features = state.get("features", {})
        atr = features.get("atr_14", features.get("atr", 0))

        if symbol.upper().startswith("XAU") or symbol.upper().startswith("XAG"):
            min_atr = 3.0
        elif "JPY" in symbol:
            min_atr = 0.10
        else:
            min_atr = 0.0010
        if not atr or atr < min_atr:
            atr = min_atr

        sl_atr_mult = self.rules.get("sl_atr_multiplier", 0.75)
        session = state.get("session", "").lower()
        session_factors = self.rules.get("position_sizing", {}).get("session_factor", {})
        sl_session_scale = session_factors.get(session, 0.75)

        avg_atr = features.get("atr_avg", features.get("atr_50", atr))
        vol_ratio = atr / avg_atr if avg_atr and avg_atr > 0 else 1.0
        vol_scaling = self.rules.get("vol_scaling", {})
        if vol_ratio < 0.7:
            vol_regime_key = "low"
        elif vol_ratio > 2.0:
            vol_regime_key = "extreme"
        elif vol_ratio > 1.3:
            vol_regime_key = "high"
        else:
            vol_regime_key = "medium"
        vol_sl_mult = vol_scaling.get("sl_factor", {}).get(vol_regime_key, 1.0)

        sl_distance = atr * sl_atr_mult * sl_session_scale * vol_sl_mult

        # ── SL ATR floor — prevent noise-triggered stops ───────────
        # Gold H1 range is $25-62; without a floor the SL was ~$4-5
        # (atr*0.56) and got stopped by normal candle noise instantly.
        # Mirror the same 1.5×ATR floor used in shared/sl_tp.py and
        # the ML autonomous pipeline.
        _sl_atr_floor = atr * 1.5
        if sl_distance < _sl_atr_floor:
            sl_distance = _sl_atr_floor

        sl_price = entry - sl_distance if verdict == "BUY" else entry + sl_distance

        risk_pct = self.rules.get("capital", {}).get(
            "max_risk_per_trade_pct",
            DEFAULT_RISK_PARAMS["max_risk_per_trade_pct"],
        )

        try:
            raw_size = await calculate_size(entry, sl_price, risk_pct)
        except Exception:
            raw_size = 0.01

        vol_factors = self.rules.get("position_sizing", {}).get("vol_factor", {})
        tilt_factors = self.rules.get("position_sizing", {}).get("tilt_factor", {})
        session_factors = self.rules.get("position_sizing", {}).get("session_factor", {})

        vol_regime = "medium"
        if atr:
            avg_atr = features.get("atr_avg", atr)
            ratio = atr / avg_atr if avg_atr else 1.0
            if ratio < 0.7:
                vol_regime = "low"
            elif ratio > 1.5:
                vol_regime = "high"
            elif ratio > 2.5:
                vol_regime = "extreme"

        vol_mult = vol_factors.get(vol_regime, 0.75)

        exposure = {}
        try:
            exposure = await check_exposure()
        except Exception:
            pass
        tilt_status = exposure.get("tilt_status", "normal").lower()
        tilt_mult = tilt_factors.get(tilt_status, 1.0)

        session = state.get("session", "").lower()
        session_mult = session_factors.get(session, 0.75)

        kelly_fraction = (conviction / 100.0) * (0.8 if _AGGRESSIVE_TRADING else 0.5)
        adjusted_size = raw_size * vol_mult * tilt_mult * session_mult * kelly_fraction

        config_cap = self.rules.get("capital", {}).get("position_size_cap_lots", 0)
        equity_cap = dynamic_lot_cap(equity)
        cap = config_cap if config_cap > 0 else equity_cap

        # ── Hard balance-based lot cap (mirrors position_sizer.py) ──
        # dynamic_lot_cap(equity/5000) is too generous ($8500 → 1.70).
        # Both values read from risk_rules.yaml → capital section so
        # nothing is hardcoded — adjust the YAML to change behaviour.
        _cap_cfg = self.rules.get("capital", {})
        _equity_divisor = float(_cap_cfg.get("lot_cap_equity_divisor", 40_000))
        _hard_ceiling = float(_cap_cfg.get("lot_cap_hard_ceiling", 0.50))
        _balance_max_lots = max(0.01, round(equity / _equity_divisor, 2))
        effective_cap = min(cap, _balance_max_lots, _hard_ceiling)

        final_size = max(0.01, min(adjusted_size, effective_cap))
        final_size = round(final_size, 2)

        corr_ok = True
        try:
            corr_result = await check_correlation(symbol, exposure.get("positions", []))
            if corr_result.get("violation"):
                corr_ok = False
        except Exception:
            pass

        if not corr_ok and not _AGGRESSIVE_TRADING:
            max_corr = self.rules.get("exposure", {}).get("max_correlation_between_positions", 0.7)
            state["risk_decision"] = "REJECT"
            state["position_size"] = 0.0
            state["risk_reasoning"] = f"Correlation exposure exceeds {max_corr} with existing positions."
        elif final_size < raw_size * (0.35 if _AGGRESSIVE_TRADING else 0.5):
            state["risk_decision"] = "MODIFY"
            state["position_size"] = final_size
            state["risk_reasoning"] = (
                f"Size reduced from {raw_size:.2f} to {final_size:.2f} lots "
                f"(equity=${equity:.0f}, cap={cap:.2f}). "
                f"Factors: vol={vol_mult}, tilt={tilt_mult}, session={session_mult}, kelly={kelly_fraction:.2f}"
            )
        else:
            state["risk_decision"] = "APPROVE"
            state["position_size"] = final_size
            state["risk_reasoning"] = (
                f"Approved {final_size:.2f} lots "
                f"(equity=${equity:.0f}, cap={cap:.2f}). "
                f"Factors: vol={vol_mult}, tilt={tilt_mult}, session={session_mult}, kelly={kelly_fraction:.2f}"
            )

        context_for_llm = (
            f"Symbol: {symbol}\n"
            f"Verdict: {verdict}, Conviction: {conviction}\n"
            f"Account equity: ${equity:.2f}\n"
            f"Entry (ref close): {entry:.2f}\n"
            f"Stop-loss: price={sl_price:.2f}, distance={sl_distance:.2f} "
            f"({sl_atr_mult}xATR x session {sl_session_scale} x vol {vol_sl_mult}); "
            f"ATR={atr:.2f}. Risk per trade capped at {risk_pct:.1f}% of equity.\n"
            f"Circuit breakers: {json.dumps(breaker_results, default=str)}\n"
            f"Position size: raw={raw_size:.4f}, final={final_size:.4f}, cap={cap:.2f}\n"
            f"Volatility regime: {vol_regime} (mult={vol_mult})\n"
            f"Tilt status: {tilt_status} (mult={tilt_mult})\n"
            f"Session: {session} (mult={session_mult})\n"
            f"Kelly fraction: {kelly_fraction:.2f}\n"
            f"Correlation check: {'OK' if corr_ok else 'VIOLATION'}\n"
            f"Current exposure: {json.dumps(exposure, default=str)}\n"
            f"Risk decision: {state['risk_decision']}\n"
            f"Reasoning: {state['risk_reasoning']}"
        )

        try:
            llm = self._get_llm(fast=True)
            prompt = self._load_prompt("risk_manager")
            chain = prompt | llm
            response = await asyncio.wait_for(chain.ainvoke({"input": context_for_llm}), timeout=30)
            llm_reasoning = response.content if hasattr(response, "content") else str(response)
            state["risk_reasoning"] += f"\n\nLLM Review: {llm_reasoning[:400]}"
        except Exception:
            pass

        state["messages"].append({
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "type": "risk",
            "decision": state["risk_decision"],
            "size": state["position_size"],
            "equity": equity,
            "lot_cap": cap,
            "content": state["risk_reasoning"][:500],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        await self._publish_message(
            f"Risk: {state['risk_decision']} | Size: {state['position_size']} | Equity: ${equity:.0f}",
            metadata={"decision": state["risk_decision"], "size": state["position_size"], "equity": equity},
            state=state,
        )
        await self._log_reasoning(state["risk_reasoning"], state=state)

        return state

    async def _check_circuit_breakers(self, state: AgentState, equity: float) -> list[dict]:
        """Check ALL inviolable circuit breakers including dynamic limits."""
        breakers: list[dict] = []

        # ── Kill switch (shared with nexus_risk via Redis) ─────────
        # When anti-tilt detects emotional trading, nexus_risk sets
        # system:kill_switch in Redis with a 30-min TTL. GenAI trades
        # used to bypass this entirely because they never went through
        # nexus_risk's _evaluate_proposal().
        try:
            if self.redis_client:
                ks = await self.redis_client.cache_get("system:kill_switch")
                if ks:
                    breakers.append({
                        "name": "kill_switch",
                        "triggered": True,
                        "reason": f"System kill switch active: {ks.get('reason', 'unknown')}",
                        "value": ks,
                    })
        except Exception:
            pass

        # ── Circuit-breaker: win-rate on last 10 trades ────────────
        # Mirrors _check_circuit_breaker() in nexus_risk/main.py.
        # If WR < 20% on 8+ recent results, pause all trading.
        try:
            if self.redis_client:
                raw = await self.redis_client.r.lrange("nexus:risk:recent_results", 0, 9)
                if raw and len(raw) >= 8:
                    results = [float(x) for x in raw]
                    wins = sum(1 for r in results if r > 0)
                    wr = wins / len(results)
                    if wr < 0.20:
                        breakers.append({
                            "name": "circuit_breaker_wr",
                            "triggered": True,
                            "reason": f"Win rate {wr:.0%} on last {len(results)} trades — system paused",
                            "value": wr,
                        })
        except Exception:
            pass

        try:
            exposure = await check_exposure()
        except Exception:
            exposure = {}

        max_daily_loss = self.rules.get("capital", {}).get("max_daily_loss_pct", 5.0)
        daily_loss = exposure.get("daily_loss_pct", 0.0)
        breakers.append({
            "name": "daily_loss_limit",
            "triggered": daily_loss >= max_daily_loss,
            "reason": f"Daily loss {daily_loss:.1f}% >= limit {max_daily_loss}%",
            "value": daily_loss,
        })

        max_weekly_dd = self.rules.get("capital", {}).get("max_weekly_drawdown_pct", 8.0)
        weekly_dd = exposure.get("weekly_dd_pct", 0.0)
        breakers.append({
            "name": "weekly_drawdown",
            "triggered": weekly_dd >= max_weekly_dd,
            "reason": f"Weekly DD {weekly_dd:.1f}% >= limit {max_weekly_dd}%",
            "value": weekly_dd,
        })

        max_positions = self.rules.get("exposure", {}).get("max_simultaneous_positions", 3)
        open_positions = exposure.get("open_positions", 0)
        breakers.append({
            "name": "max_positions",
            "triggered": open_positions >= max_positions,
            "reason": f"Open positions {open_positions} >= max {max_positions}",
            "value": open_positions,
        })

        trade_controls = self.rules.get("trade_controls", {})

        config_limit = trade_controls.get("daily_trade_limit", 0)
        effective_limit = config_limit if config_limit > 0 else dynamic_daily_limit(equity)
        trades_today = await get_trade_count_today()
        breakers.append({
            "name": "daily_trade_limit",
            "triggered": trades_today >= effective_limit,
            "reason": f"Daily trades {trades_today} >= limit {effective_limit} (equity=${equity:.0f})",
            "value": trades_today,
        })

        cooldown_sec = trade_controls.get("cooldown_between_trades_sec", 120)
        elapsed = seconds_since_last_trade()
        breakers.append({
            "name": "trade_cooldown",
            "triggered": elapsed < cooldown_sec,
            "reason": f"Cooldown: {elapsed:.0f}s elapsed, need {cooldown_sec}s between trades",
            "value": elapsed,
        })

        min_conviction = trade_controls.get("min_conviction_to_trade", 60)
        conviction = state.get("verdict_conviction", 0)
        breakers.append({
            "name": "min_conviction",
            "triggered": conviction < min_conviction,
            "reason": f"Conviction {conviction} < minimum {min_conviction}",
            "value": conviction,
        })

        anti_tilt = self.rules.get("anti_tilt", {})
        consecutive_losses = exposure.get("consecutive_losses", 0)
        pause_threshold = anti_tilt.get("consecutive_losses_pause", 5)
        breakers.append({
            "name": "anti_tilt",
            "triggered": consecutive_losses >= pause_threshold,
            "reason": f"Consecutive losses {consecutive_losses} >= pause threshold {pause_threshold}",
            "value": consecutive_losses,
        })

        now = datetime.now(timezone.utc)
        friday_close = self.rules.get("calendar", {}).get("friday_close_utc", "20:00")
        if now.weekday() == 4:
            close_hour = int(friday_close.split(":")[0])
            if now.hour >= close_hour:
                breakers.append({
                    "name": "friday_close",
                    "triggered": True,
                    "reason": f"Friday close after {friday_close} UTC",
                    "value": now.hour,
                })

        # ── Trade coherence: no opposing or duplicate positions ──
        verdict = state.get("verdict", "NO_TRADE")
        open_positions = state.get("open_positions", [])
        if not open_positions:
            try:
                open_positions = await get_open_positions(state["symbol"])
            except Exception:
                open_positions = []

        symbol_positions = [
            p for p in open_positions
            if p.get("symbol", "").upper() == state["symbol"].upper()
        ]

        if symbol_positions:
            existing_sides = {
                str(p.get("side", p.get("type", ""))).upper()
                for p in symbol_positions
            }
            normalized_sides = set()
            for s in existing_sides:
                if s in ("BUY", "LONG", "0"):
                    normalized_sides.add("BUY")
                elif s in ("SELL", "SHORT", "1"):
                    normalized_sides.add("SELL")

            if verdict in ("BUY", "SELL") and normalized_sides:
                opposite = "SELL" if verdict == "BUY" else "BUY"
                if opposite in normalized_sides:
                    breakers.append({
                        "name": "opposing_position",
                        "triggered": True,
                        "reason": (
                            f"Cannot {verdict} {state['symbol']}: "
                            f"opposing {opposite} position already open"
                        ),
                        "value": list(normalized_sides),
                    })

                if verdict in normalized_sides:
                    breakers.append({
                        "name": "duplicate_position",
                        "triggered": True,
                        "reason": (
                            f"Cannot {verdict} {state['symbol']}: "
                            f"same-direction position already open"
                        ),
                        "value": len(symbol_positions),
                    })

        session = state.get("session", "")
        if session.upper() == "OFF_HOURS" and not _AGGRESSIVE_TRADING and not _ALLOW_OFF_HOURS:
            breakers.append({
                "name": "off_hours",
                "triggered": True,
                "reason": "Trading during OFF_HOURS session",
                "value": session,
            })

        return breakers
