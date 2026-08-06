"""
A11 — El Director: Session Governor agent.

Determines the current trading session and sets session-specific parameters:
  - Active agents for the session
  - Eligible currency pairs
  - Max trades allowed
  - Volatility mode and size multiplier
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
import yaml

from shared.redis_client import RedisClient
from shared.sessions import detect_session_now, session_to_key

from ..state import AgentState
from .base_agent import BaseAgent

logger = structlog.get_logger(__name__)

SESSIONS_PATH = Path(__file__).resolve().parents[3] / "config" / "sessions.yaml"


def _load_sessions_config() -> dict[str, Any]:
    if SESSIONS_PATH.exists():
        with open(SESSIONS_PATH) as f:
            return yaml.safe_load(f)
    return {}


_SESSIONS_CONFIG = _load_sessions_config()

SESSION_MAPPING: dict[str, str] = {
    "sydney": "SYDNEY",
    "tokyo": "TOKYO",
    "london": "LONDON",
    "new_york": "NEW_YORK",
    "london_ny_overlap": "OVERLAP",
    "off_hours": "OFF_HOURS",
}


class SessionGovernorAgent(BaseAgent):
    def __init__(self, *, redis_client: RedisClient | None = None) -> None:
        super().__init__("A11", "Session Governor", redis_client=redis_client)

    async def invoke(self, state: AgentState) -> AgentState:
        now = datetime.now(timezone.utc)
        current_hour = now.hour

        active_session = self._determine_session(current_hour, now.weekday())
        # Align with shared.sessions (single source of truth for ML/risk)
        canonical = detect_session_now().value
        if active_session not in ("OFF_HOURS",) and canonical != "OFF_HOURS":
            active_session = canonical
        session_config = self._get_session_config(active_session)

        symbol = state["symbol"]
        eligible_pairs = session_config.get("pairs", [])
        normalized_symbol = symbol.replace("/", "")
        normalized_pairs = [p.replace("/", "") for p in eligible_pairs]

        is_eligible = (
            not eligible_pairs
            or normalized_symbol in normalized_pairs
            or symbol in eligible_pairs
        )

        max_trades = session_config.get("max_trades", 0)
        size_multiplier = session_config.get("size_multiplier", 1.0)
        min_conviction = session_config.get("min_conviction", 70)
        mode = session_config.get("mode", "normal")

        special_rules = _SESSIONS_CONFIG.get("special_rules", {})
        friday_rules = special_rules.get("friday_wind_down", {})
        sunday_rules = special_rules.get("sunday_open", {})

        is_friday_wind_down = False
        if now.weekday() == 4:
            cutoff = friday_rules.get("no_new_positions_after", "18:00")
            cutoff_hour = int(cutoff.split(":")[0])
            if current_hour >= cutoff_hour:
                is_friday_wind_down = True
                max_trades = 0
                mode = "wind_down"

        is_sunday_monitor = False
        if now.weekday() == 6:
            monitor_until = sunday_rules.get("monitor_only_until", "23:00")
            monitor_hour = int(monitor_until.split(":")[0])
            if current_hour < monitor_hour:
                is_sunday_monitor = True
                max_trades = 0
                mode = "monitoring_only"

        if not is_eligible:
            active_session = "OFF_HOURS"
            max_trades = 0
            mode = "pair_not_eligible"

        state["session"] = active_session

        context_for_llm = (
            f"Current time (UTC): {now.isoformat()}\n"
            f"Day of week: {now.strftime('%A')}\n"
            f"Active session: {active_session}\n"
            f"Session mode: {mode}\n"
            f"Symbol: {symbol}\n"
            f"Symbol eligible: {is_eligible}\n"
            f"Eligible pairs: {json.dumps(eligible_pairs)}\n"
            f"Max trades: {max_trades}\n"
            f"Size multiplier: {size_multiplier}\n"
            f"Min conviction: {min_conviction}\n"
            f"Friday wind-down: {is_friday_wind_down}\n"
            f"Sunday monitor: {is_sunday_monitor}\n"
            f"Confirm the session parameters and provide any session-specific guidance."
        )

        try:
            llm = self._get_llm(fast=True)
            prompt = self._load_prompt("session_governor")
            chain = prompt | llm
            response = await self._invoke_with_budget(chain, {"input": context_for_llm})
            llm_output = response.content if hasattr(response, "content") else str(response)
        except Exception:
            llm_output = f"Session: {active_session}, Mode: {mode}"

        state["messages"].append({
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "type": "session",
            "content": f"Session: {active_session} | Mode: {mode} | Eligible: {is_eligible}",
            "session": active_session,
            "mode": mode,
            "max_trades": max_trades,
            "eligible": is_eligible,
            "timestamp": now.isoformat(),
        })

        if self.redis:
            await self.redis.cache_set("session:state", {
                "name": active_session,
                "mode": mode,
                "max_trades": max_trades,
                "size_multiplier": size_multiplier,
                "min_conviction": min_conviction,
                "eligible_pairs": eligible_pairs,
                "friday_wind_down": is_friday_wind_down,
                "sunday_monitor": is_sunday_monitor,
            }, ttl_sec=3600)

        await self._publish_message(
            f"Session: {active_session} | Mode: {mode}",
            metadata={
                "session": active_session,
                "mode": mode,
                "max_trades": max_trades,
                "eligible": is_eligible,
            },
            state=state,
        )
        await self._log_reasoning(llm_output, state=state)

        return state

    def _determine_session(self, hour: int, weekday: int) -> str:
        """
        Session schedule (UTC):
            Sydney:   22:00 – 06:00  (wraps midnight)
            Tokyo:    00:00 – 08:00
            London:   07:00 – 16:00
            New York: 12:00 – 21:00
            Overlap:  12:00 – 16:00  (London + NY)
            Off hours: Sat after 21:00, Sunday before market reopen
        Priority: OFF_HOURS > OVERLAP > LONDON > NEW_YORK > TOKYO > SYDNEY

        Sunday reopen hour comes from config/sessions.yaml special_rules.sunday_open
        .market_open (default 22:00 UTC, i.e. Sydney open) — Sunday before that
        hour is OFF_HOURS, at/after it the market is live and falls through to
        normal session detection below.
        """
        if weekday == 5 and hour >= 21:
            return "OFF_HOURS"
        if weekday == 6:
            sunday_rules = _SESSIONS_CONFIG.get("special_rules", {}).get("sunday_open", {})
            reopen = str(sunday_rules.get("market_open", "22:00"))
            try:
                reopen_hour = int(reopen.split(":")[0])
            except (ValueError, IndexError):
                reopen_hour = 22
            if hour < reopen_hour:
                return "OFF_HOURS"

        # London–NY overlap (highest priority active session)
        if 12 <= hour < 16:
            return "OVERLAP"
        # London solo (before overlap)
        if 7 <= hour < 12:
            return "LONDON"
        # New York solo (after overlap)
        if 16 <= hour < 21:
            return "NEW_YORK"
        # Tokyo
        if 0 <= hour < 8:
            return "TOKYO"
        # Sydney (wraps around midnight)
        if hour >= 21 or hour < 7:
            return "SYDNEY"

        return "OFF_HOURS"

    def _get_session_config(self, session: str) -> dict[str, Any]:
        sessions = _SESSIONS_CONFIG.get("sessions", {})

        config_key_map = {
            "SYDNEY": "sydney",
            "TOKYO": "tokyo",
            "LONDON": "london",
            "NEW_YORK": "new_york",
            "OVERLAP": "london_ny_overlap",
            "OFF_HOURS": "off_hours",
        }
        key = config_key_map.get(session, session_to_key(session))
        return sessions.get(key, {
            "pairs": [],
            "max_trades": 0,
            "size_multiplier": 0.0,
            "min_conviction": 100,
            "mode": "off",
        })
