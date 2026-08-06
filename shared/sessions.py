"""
Unified trading session detection — single source of truth.

All services (agents, ML traders, trade_memory, risk) must import from here.
Schedule aligned with config/sessions.yaml (UTC).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum, unique


@unique
class TradingSession(StrEnum):
    SYDNEY = "SYDNEY"
    TOKYO = "TOKYO"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"
    OVERLAP = "OVERLAP"
    OFF_HOURS = "OFF_HOURS"


# UTC hour ranges (inclusive start, exclusive end unless noted)
_SESSION_SCHEDULE: dict[TradingSession, tuple[int, int]] = {
    TradingSession.SYDNEY: (22, 6),    # wraps midnight
    TradingSession.TOKYO: (0, 8),
    TradingSession.LONDON: (7, 16),
    TradingSession.NEW_YORK: (12, 21),
    TradingSession.OVERLAP: (12, 16),
}


def detect_session(utc_hour: int | None = None) -> TradingSession:
    """Detect active session from UTC hour. OVERLAP takes priority 12-16."""
    if utc_hour is None:
        utc_hour = datetime.now(timezone.utc).hour

    if 12 <= utc_hour < 16:
        return TradingSession.OVERLAP
    if 7 <= utc_hour < 12:
        return TradingSession.LONDON
    if 16 <= utc_hour < 21:
        return TradingSession.NEW_YORK
    if 0 <= utc_hour < 8:
        return TradingSession.TOKYO
    if utc_hour >= 22 or utc_hour < 6:
        return TradingSession.SYDNEY
    return TradingSession.OFF_HOURS


def detect_session_now() -> TradingSession:
    return detect_session(datetime.now(timezone.utc).hour)


def session_to_key(session: TradingSession | str) -> str:
    """Normalize session name for YAML/config lookups (lowercase)."""
    s = str(session).upper()
    mapping = {
        "OVERLAP": "london_ny_overlap",
        "NEW_YORK": "new_york",
        "OFF_HOURS": "off_hours",
    }
    return mapping.get(s, s.lower())


# Session size multipliers for risk (aligned with risk_rules.yaml)
SESSION_SIZE_FACTORS: dict[str, float] = {
    "sydney": 0.5,
    "tokyo": 0.75,
    "london": 1.0,
    "new_york": 1.0,
    "overlap": 1.25,
    "london_ny_overlap": 1.25,
    "off_hours": 0.0,
    "unknown": 0.5,
}


def get_session_size_factor(session: TradingSession | str) -> float:
    key = session_to_key(session)
    return SESSION_SIZE_FACTORS.get(key, 0.5)
