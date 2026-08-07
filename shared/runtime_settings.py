"""
Nexus Trader — Runtime Settings (front-configurable, shared by all services).

A single source of truth for the operational knobs the user can change live
from the dashboard WITHOUT restarting services or editing YAML:

  - risk budget (daily loss %, risk per trade %)
  - position / trade caps (max open positions, max daily trades)
  - scalping timing (min hold minutes, max hold minutes)
  - strategy research aggressiveness (min candidates, search rounds)

The settings live in Redis under a single key so both the ML pipeline
(nexus-ml) and the risk + agent pipeline (nexus-risk / nexus-agents) read the
exact same values. This unifies what used to be two divergent config sources
(``trading_mode.py`` vs ``risk_rules.yaml``).

Reads are tolerant: if Redis is unavailable the in-code DEFAULTS are returned,
so the system always has sane behaviour.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

try:
    from shared.redis_client import RedisClient
except Exception:  # pragma: no cover - allows import in isolated tests
    RedisClient = None  # type: ignore[assignment]

# Raw Redis key (no prefix) shared across every service.
SETTINGS_KEY = "nexus:runtime:settings"

# Persist effectively forever; writers re-set on every update.
_TTL_SEC = 10 * 365 * 24 * 3600

# ── Defaults (user-requested) ────────────────────────────────────────────────
# These are the values the system boots with the first time, before the user
# touches anything from the dashboard.
DEFAULTS: dict[str, Any] = {
    # Risk budget
    "risk_per_trade_pct": 1.0,      # % of equity risked per trade
    "max_daily_loss_pct": 20.0,     # daily loss budget before trades pause
    "max_weekly_dd_pct": 25.0,      # weekly drawdown budget
    # Caps
    "max_open_positions": 5,        # simultaneous positions
    "max_daily_trades": 5,          # trades per day
    # Scalping timing (minutes)
    "min_hold_minutes": 5,          # do not exit before this many minutes
    "max_hold_minutes": 60,         # force-exit after this many minutes
    # Conviction gate (0..1). null/None = use the per-session default.
    "min_conviction_override": None,
    # Strategy research aggressiveness
    "research_min_candidates": 4,   # strategies designed per research cycle
    "research_max_search_rounds": 3,  # keep searching until a good one or N rounds
    "research_interval_sec": 1800,  # base seconds between research cycles
    # Multi-timeframe
    "require_mtf_confirmation": True,
    "min_mtf_agreement": 1,
    # Operational timeframe (synced on scalping/swing mode switch)
    "operational_tf": "M5",
    "trading_mode_type": "scalping",
    # Auto-select M1/M5/M15 from session + volatility + spread (scalping only)
    "auto_tf_selection": True,
    # AI provider for the GenAI agents — front-selectable, live (no restart).
    # deepseek (default) | openai (GPT) | gemini | local. See shared/llm_factory.
    # Seeded from NEXUS_LLM_PROVIDER so .env sets the base; the dashboard overrides it.
    "llm_provider": (os.getenv("NEXUS_LLM_PROVIDER") or "deepseek").strip().lower(),
}

# Bounds to keep front-supplied values sane (defensive clamping).
_BOUNDS: dict[str, tuple[float, float]] = {
    "risk_per_trade_pct": (0.1, 5.0),
    "max_daily_loss_pct": (1.0, 50.0),
    "max_weekly_dd_pct": (1.0, 60.0),
    "max_open_positions": (1, 20),
    "max_daily_trades": (1, 100),
    "min_hold_minutes": (0, 240),
    "max_hold_minutes": (1, 1440),
    "research_min_candidates": (1, 12),
    "research_max_search_rounds": (1, 8),
    "research_interval_sec": (120, 7200),
    "min_mtf_agreement": (0, 3),
}

_VALID_TFS = {"M1", "M5", "M15", "M30", "H1", "H4", "D1"}

# Process-local cache so synchronous code paths (e.g. trading_mode helpers) can
# read the most recent values without an await. Refreshed on every async read.
_cache: dict[str, Any] = dict(DEFAULTS)

# Module-level shared client so we DON'T pay the connection-establishment cost
# (which can be ~2s per new connection on Windows) on every settings read.
_shared_client: "RedisClient | None" = None
_last_fetch: float = 0.0
# Throttle remote reads: callers may invoke get_settings() in hot loops; serve
# the cached value if it was refreshed very recently.
_FETCH_TTL_SEC = 5.0


async def _get_shared_client() -> "RedisClient | None":
    """Return a lazily-created, reused, connected RedisClient (or None)."""
    global _shared_client
    if RedisClient is None:
        return None
    if _shared_client is None:
        try:
            _shared_client = RedisClient()
            await _shared_client.connect()
        except Exception:
            _shared_client = None
    return _shared_client


def _coerce(key: str, value: Any) -> Any:
    """Coerce + clamp a single setting; fall back to default on bad input."""
    default = DEFAULTS.get(key)
    if key == "min_conviction_override":
        if value in (None, "", "null"):
            return None
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return None
    if key == "llm_provider":
        aliases = {"cloud": "deepseek", "gpt": "openai", "chatgpt": "openai", "google": "gemini"}
        v = str(value or "").strip().lower()
        v = aliases.get(v, v)
        return v if v in ("deepseek", "openai", "gemini", "local") else "deepseek"
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            v: float = float(value)
        except (TypeError, ValueError):
            return default
        lo, hi = _BOUNDS.get(key, (v, v))
        return int(max(lo, min(hi, v)))
    if isinstance(default, float):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return default
        lo, hi = _BOUNDS.get(key, (v, v))
        return float(max(lo, min(hi, v)))
    return value


def merge_with_defaults(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Return a complete settings dict: defaults overlaid with valid overrides."""
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in DEFAULTS:
                out[k] = _coerce(k, v)
    return out


def get_cached() -> dict[str, Any]:
    """Synchronous best-effort read of the last-known settings (no await)."""
    return dict(_cache)


# Alias used by nexus-agents (position_manager, main)
get_cached_settings = get_cached


async def get_settings(
    redis: "RedisClient | None" = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Async read from Redis, merged with defaults. Updates the local cache.

    Reuses a shared connection and throttles remote reads (``_FETCH_TTL_SEC``)
    so hot-loop callers don't hammer Redis. Pass ``force=True`` to bypass the
    throttle (e.g. right after an update).
    """
    global _cache, _last_fetch
    import time as _t

    if not force and (_t.monotonic() - _last_fetch) < _FETCH_TTL_SEC:
        return dict(_cache)

    r = redis or await _get_shared_client()
    try:
        raw = await r.cache_get(SETTINGS_KEY, prefix=False) if r else None
        merged = merge_with_defaults(raw if isinstance(raw, dict) else None)
        _cache = merged
        _last_fetch = _t.monotonic()
        return merged
    except Exception:
        return dict(_cache)


async def update_settings(
    patch: dict[str, Any],
    redis: "RedisClient | None" = None,
) -> dict[str, Any]:
    """Merge a partial patch into current settings and persist to Redis."""
    global _cache
    current = await get_settings(redis, force=True)
    merged = dict(current)
    for k, v in (patch or {}).items():
        if k in DEFAULTS:
            merged[k] = _coerce(k, v)
    r = redis or await _get_shared_client()
    try:
        if r:
            await r.cache_set(SETTINGS_KEY, merged, ttl_sec=_TTL_SEC, prefix=False)
        _cache = merged
        return merged
    except Exception:
        _cache = merged
        return merged


__all__ = [
    "DEFAULTS",
    "SETTINGS_KEY",
    "get_settings",
    "get_cached",
    "get_cached_settings",
    "update_settings",
    "merge_with_defaults",
]
