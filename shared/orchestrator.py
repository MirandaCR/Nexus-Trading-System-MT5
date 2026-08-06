"""
Trade orchestration — single priority rules between GenAI agents and ML autonomous.

Prevents conflicting dual-brain trades on the same symbol.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

PRIORITY_GENAI = "genai_agents"
PRIORITY_ML = "autonomous_ml"

# GenAI agents take priority by default (richer context). Set NEXUS_ML_PRIORITY=1 to invert.
_ML_PRIORITY = os.getenv("NEXUS_ML_PRIORITY", "0").lower() in ("1", "true", "yes")
_SIGNAL_TTL_SEC = int(os.getenv("NEXUS_ACTIVE_SIGNAL_TTL", "120"))


async def claim_active_signal(
    redis: Any,
    symbol: str,
    source: str,
    payload: dict[str, Any],
) -> bool:
    """Register this source as the active decision-maker for symbol."""
    key = f"nexus:active_signal:{symbol.upper()}"
    data = {
        "source": source,
        "direction": payload.get("direction", "NO_TRADE"),
        "confidence": payload.get("confidence", 0),
        "ts": time.time(),
        "payload": payload,
    }
    await redis.set(key, json.dumps(data), ex=_SIGNAL_TTL_SEC)
    return True


async def should_defer(
    redis: Any,
    symbol: str,
    source: str,
) -> tuple[bool, str]:
    """Return (should_defer, reason) if another higher-priority source is active."""
    key = f"nexus:active_signal:{symbol.upper()}"
    try:
        raw = await redis.get(key)
        if not raw:
            return False, ""
        data = json.loads(raw)
        active_source = data.get("source", "")
        ts = float(data.get("ts", 0))
        if time.time() - ts > _SIGNAL_TTL_SEC:
            return False, ""

        if active_source == source:
            return False, ""

        # Priority logic
        if _ML_PRIORITY:
            winner = PRIORITY_ML
        else:
            winner = PRIORITY_GENAI

        if active_source == winner and source != winner:
            return True, f"Deferred to active {active_source} (priority={winner})"
        return False, ""
    except Exception:
        return False, ""


def get_priority_source() -> str:
    return PRIORITY_ML if _ML_PRIORITY else PRIORITY_GENAI
