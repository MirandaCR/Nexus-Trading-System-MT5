"""Tests for automatic operational timeframe selection."""
from __future__ import annotations

from services.nexus_ml.traders.trading_mode import (
    Session,
    TradingMode,
    get_mode_config,
    select_operational_tf,
    get_confirmation_tfs_for,
)


def _features(vol_regime: str = "MEDIUM", atr: float = 10.0, atr_sma: float = 10.0) -> dict:
    return {
        "vol_regime": vol_regime,
        "atr_14": atr,
        "atr_sma": atr_sma,
        "close": 2400.0,
    }


def test_swing_mode_keeps_fixed_tf():
    cfg = get_mode_config(TradingMode.SWING)
    sel = select_operational_tf(cfg, Session.OVERLAP, _features(), auto=True)
    assert sel.operational_tf == "M15"
    assert sel.auto_selected is False


def test_extreme_vol_uses_m5():
    cfg = get_mode_config(TradingMode.SCALPING)
    sel = select_operational_tf(
        cfg, Session.OVERLAP, _features("EXTREME", atr=20, atr_sma=10), auto=True,
    )
    assert sel.operational_tf == "M5"
    assert "EXTREME" in sel.reason


def test_overlap_calm_tight_spread_uses_m1():
    cfg = get_mode_config(TradingMode.SCALPING)
    sel = select_operational_tf(
        cfg,
        Session.OVERLAP,
        _features("LOW"),
        auto=True,
        current_spread=1.5,
        avg_spread=2.0,
    )
    assert sel.operational_tf == "M1"
    assert sel.confirmation_tfs == get_confirmation_tfs_for("M1", TradingMode.SCALPING)


def test_tokyo_never_m1():
    cfg = get_mode_config(TradingMode.SCALPING)
    sel = select_operational_tf(
        cfg,
        Session.TOKYO,
        _features("LOW"),
        auto=True,
        current_spread=1.0,
        avg_spread=2.0,
    )
    assert sel.operational_tf == "M5"
