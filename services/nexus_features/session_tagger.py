"""
Market-session tagger.

Tags each bar / timestamp with the active forex trading session.
Session windows are defined in UTC and sourced from shared constants
with fallback defaults matching the project's sessions.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import pandas as pd

from shared.constants import SessionName

# UTC hour ranges (start inclusive, end exclusive).  Wrap-around handled below.
SESSION_SCHEDULE: dict[str, tuple[int, int]] = {
    SessionName.SYDNEY:   (22, 6),
    SessionName.TOKYO:    (0, 8),
    SessionName.LONDON:   (7, 16),
    SessionName.NEW_YORK: (12, 21),
}

_OVERLAP_RANGES: list[tuple[str, int, int]] = [
    ("TOKYO_LONDON",  7,  8),
    ("LONDON_NY",    12, 16),
]


@dataclass(frozen=True)
class SessionInfo:
    name: str
    is_overlap: bool
    overlap_name: str | None = None
    start_utc: int = 0
    end_utc: int = 0


# ── Helpers ──────────────────────────────────────────────────────────────────

def _in_range(hour: int, start: int, end: int) -> bool:
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


# ── Public API ───────────────────────────────────────────────────────────────

def tag_session(timestamp: datetime) -> str:
    """Return the primary session name for a given UTC timestamp."""
    h = timestamp.hour
    for name, (start, end) in SESSION_SCHEDULE.items():
        if _in_range(h, start, end):
            return name
    return SessionName.OFF_HOURS


def tag_dataframe(df: pd.DataFrame, ts_col: str = "timestamp") -> pd.DataFrame:
    """Add ``session`` column to *df* based on the timestamp column."""
    df = df.copy()
    if ts_col not in df.columns:
        if isinstance(df.index, pd.DatetimeIndex):
            hours = df.index.hour
        else:
            df["session"] = SessionName.OFF_HOURS
            return df
    else:
        hours = pd.to_datetime(df[ts_col], utc=True).dt.hour

    sessions = pd.Series(SessionName.OFF_HOURS, index=df.index, dtype="object")
    for name, (start, end) in SESSION_SCHEDULE.items():
        if start < end:
            mask = (hours >= start) & (hours < end)
        else:
            mask = (hours >= start) | (hours < end)
        sessions[mask] = name

    overlap_mask = pd.Series(False, index=df.index)
    for _, ol_start, ol_end in _OVERLAP_RANGES:
        overlap_mask |= (hours >= ol_start) & (hours < ol_end)
    sessions[overlap_mask] = SessionName.OVERLAP

    df["session"] = sessions
    return df


def get_current_session() -> SessionInfo:
    """Return a ``SessionInfo`` for the current UTC time."""
    now = datetime.now(timezone.utc)
    h = now.hour
    primary = tag_session(now)
    overlap = is_overlap(now)
    overlap_name: str | None = None
    if overlap:
        for oname, s, e in _OVERLAP_RANGES:
            if s <= h < e:
                overlap_name = oname
                break
    start, end = SESSION_SCHEDULE.get(primary, (0, 0))
    return SessionInfo(
        name=primary,
        is_overlap=overlap,
        overlap_name=overlap_name,
        start_utc=start,
        end_utc=end,
    )


def is_overlap(ts: datetime | None = None) -> bool:
    """Return ``True`` if *ts* (default now) falls inside a session overlap."""
    h = (ts or datetime.now(timezone.utc)).hour
    return any(s <= h < e for _, s, e in _OVERLAP_RANGES)
