"""
Unified SL/TP calculator — used by agents, ML traders, and risk service.

All stop/target distances derive from ATR, session, volatility regime, and asset specs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.asset_specs import get_asset_spec, round_price


@dataclass
class SLTPResult:
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    sl_distance: float
    tp1_distance: float
    tp2_distance: float
    trailing_stop: float
    vol_regime: str
    vol_ratio: float
    session_scale: float
    digits: int


def classify_vol_regime(atr: float, avg_atr: float) -> tuple[str, float]:
    if avg_atr and avg_atr > 0:
        vol_ratio = atr / avg_atr
    else:
        vol_ratio = 1.0
    if vol_ratio < 0.7:
        return "low", vol_ratio
    if vol_ratio > 2.0:
        return "extreme", vol_ratio
    if vol_ratio > 1.3:
        return "high", vol_ratio
    return "medium", vol_ratio


def compute_sltp(
    *,
    symbol: str,
    side: str,
    entry: float,
    atr: float,
    avg_atr: float = 0.0,
    session: str = "unknown",
    rules: dict[str, Any] | None = None,
    sl_atr_mult: float | None = None,
    min_rr: float | None = None,
) -> SLTPResult:
    """Compute SL and 3-tier TPs for a trade."""
    rules = rules or {}
    spec = get_asset_spec(symbol)
    atr = max(atr, spec.min_atr)
    avg_atr = avg_atr if avg_atr and avg_atr > 0 else atr

    tp_structure = rules.get("tp_structure", {})
    session_key = session.lower().replace("london_ny_overlap", "overlap")
    session_scale = tp_structure.get("session_scaling", {}).get(
        session_key,
        tp_structure.get("session_scaling", {}).get(session.lower(), 0.7),
    )

    vol_regime, vol_ratio = classify_vol_regime(atr, avg_atr)
    vol_scaling = rules.get("vol_scaling", {})
    vol_sl_factors = vol_scaling.get("sl_factor", {
        "low": 1.0, "medium": 1.0, "high": 0.8, "extreme": 0.6,
    })
    vol_tp_factors = vol_scaling.get("tp_factor", {
        "low": 0.8, "medium": 1.0, "high": 1.2, "extreme": 1.0,
    })
    vol_sl_mult = vol_sl_factors.get(vol_regime, 1.0)
    vol_tp_mult = vol_tp_factors.get(vol_regime, 1.0)

    sl_mult = sl_atr_mult if sl_atr_mult is not None else rules.get("sl_atr_multiplier", 0.75)
    sl_raw = atr * sl_mult * session_scale * vol_sl_mult
    sl_min_dist = spec.min_sl_pips * spec.pip_size

    # Floor: SL must be at least 1.5× current ATR so normal noise doesn't
    # trigger it within seconds. Previously gold trades had SL of $3-5 when
    # the H1 range was $25-60, getting stopped by noise instantly.
    sl_atr_floor = atr * 1.5
    sl_min_dist = max(sl_min_dist, sl_atr_floor)

    max_sl_dist = max(sl_min_dist, avg_atr * 2.5)
    sl_dist = min(max(sl_raw, sl_min_dist), max_sl_dist)

    side_u = side.upper()
    if side_u == "BUY":
        sl = entry - sl_dist
    else:
        sl = entry + sl_dist

    sl_distance = abs(entry - sl)
    rr = min_rr if min_rr is not None else rules.get("execution", {}).get("min_risk_reward", 1.5)

    tp1_atr = tp_structure.get("tp1", {}).get("atr_multiplier", 1.0)
    tp2_atr = tp_structure.get("tp2", {}).get("atr_multiplier", 1.8)
    tp_min = spec.min_tp_pips * spec.pip_size

    tp1_dist = max(atr * tp1_atr * session_scale * vol_tp_mult, sl_distance * rr, tp_min)
    tp2_dist = max(atr * tp2_atr * session_scale * vol_tp_mult, sl_distance * rr * 1.5, tp_min * 2)

    if side_u == "BUY":
        tp1 = entry + tp1_dist
        tp2 = entry + tp2_dist
        tp3 = entry + tp2_dist * 1.5
    else:
        tp1 = entry - tp1_dist
        tp2 = entry - tp2_dist
        tp3 = entry - tp2_dist * 1.5

    trail_mult = rules.get("trailing_stop", {}).get("trail_atr_multiplier", 0.4)
    trailing = atr * trail_mult * session_scale * vol_tp_mult

    return SLTPResult(
        entry=round_price(symbol, entry),
        sl=round_price(symbol, sl),
        tp1=round_price(symbol, tp1),
        tp2=round_price(symbol, tp2),
        tp3=round_price(symbol, tp3),
        sl_distance=sl_dist,
        tp1_distance=tp1_dist,
        tp2_distance=tp2_dist,
        trailing_stop=round_price(symbol, trailing),
        vol_regime=vol_regime,
        vol_ratio=vol_ratio,
        session_scale=session_scale,
        digits=spec.digits,
    )
