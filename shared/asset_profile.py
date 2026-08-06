"""
AssetProfile — trading-specific parameters per instrument.

Extends shared/asset_specs.py (price/pip mechanics) with trading behaviour:
session weights, ATR multipliers, signal thresholds, cross-asset correlators.

Usage:
    from shared.asset_profile import get_profile

    profile = get_profile("XAUUSD")
    profile.max_spread_pips        # → 5.0
    profile.sl_atr_multiplier      # → 1.5
    profile.cross_asset_correlators # → ["DXY", "US10Y"]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any


@dataclass(frozen=True)
class AssetProfile:
    symbol: str

    # ── Price mechanics (pip / spread / digits) ───────────────────────────
    pip_value: float = 0.0001          # price distance of 1 pip
    digits: int = 5
    contract_size: float = 100_000.0
    asset_class: str = "forex"         # forex / commodity / index / crypto

    # ── Risk defaults (all overridable via runtime settings) ──────────────
    max_spread_pips: float = 3.0       # reject entry above this spread
    sl_atr_multiplier: float = 1.5     # SL = entry ± sl_atr_mult × ATR
    tp_atr_multiplier: float = 3.0     # TP = entry ± tp_atr_mult × ATR
    min_rr_ratio: float = 2.0          # minimum acceptable Reward/Risk
    risk_per_trade: float = 0.005      # 0.5% account per trade
    max_daily_trades: int = 10
    max_hold_bars_scalping: int = 15
    max_hold_bars_swing: int = 96      # 96 × M15 ≈ 24h
    cooling_off_streak: int = 3

    # ── Session weights (higher = better for this asset) ─────────────────
    session_weights: dict[str, float] = field(default_factory=lambda: {
        "TOKYO": 0.5, "LONDON": 1.0, "NEW_YORK": 1.0, "OFF_HOURS": 0.3,
    })

    # ── Signal quality thresholds ─────────────────────────────────────────
    quality_score_threshold: float = 60.0  # default minimum (overridden by TradeMemory)
    conviction_min: float = 0.52
    min_mtf_agreement: int = 2         # how many HTF TFs must agree
    signal_cooldown_sec: int = 60

    # ── Cross-asset correlators ───────────────────────────────────────────
    cross_asset_correlators: tuple[str, ...] = ()   # e.g. ("DXY", "US10Y")
    cross_asset_inverse: dict[str, bool] = field(default_factory=dict)
    # e.g. {"DXY": True} means DXY↑ → asset↓

    # ── Tick imbalance sensitivity ────────────────────────────────────────
    tick_imbalance_window: int = 20   # bars for imbalance rolling window
    tick_imbalance_signal: float = 0.65  # above = buy pressure, below 0.35 = sell

    # ── ATR / volatility ─────────────────────────────────────────────────
    atr_period: int = 14
    min_atr: float = 0.001
    high_vol_multiplier: float = 1.3  # scale SL in HIGH_VOL regime

    # ── Display ──────────────────────────────────────────────────────────
    display_name: str = ""
    quote_currency: str = "USD"
    price_format: str = ".2f"


# ── Built-in profiles ─────────────────────────────────────────────────────────

_PROFILES: dict[str, AssetProfile] = {

    "XAUUSD": AssetProfile(
        symbol="XAUUSD",
        pip_value=0.01,
        digits=2,
        contract_size=100.0,
        asset_class="commodity",
        max_spread_pips=5.0,
        sl_atr_multiplier=1.5,
        tp_atr_multiplier=3.0,
        min_rr_ratio=2.0,
        risk_per_trade=0.005,
        max_daily_trades=8,
        max_hold_bars_scalping=12,
        max_hold_bars_swing=72,
        cooling_off_streak=3,
        session_weights={"TOKYO": 0.4, "LONDON": 1.0, "NEW_YORK": 1.0, "OFF_HOURS": 0.2},
        quality_score_threshold=62.0,
        conviction_min=0.54,
        min_mtf_agreement=2,
        signal_cooldown_sec=60,
        cross_asset_correlators=("DXY", "US10Y"),
        cross_asset_inverse={"DXY": True, "US10Y": True},  # gold falls when DXY/yields rise
        tick_imbalance_window=20,
        tick_imbalance_signal=0.62,
        atr_period=14,
        min_atr=3.0,
        high_vol_multiplier=1.4,
        display_name="Gold / USD",
        quote_currency="USD",
        price_format=".2f",
    ),

    "EURUSD": AssetProfile(
        symbol="EURUSD",
        pip_value=0.0001,
        digits=5,
        contract_size=100_000.0,
        asset_class="forex",
        max_spread_pips=2.0,
        sl_atr_multiplier=1.2,
        tp_atr_multiplier=2.5,
        min_rr_ratio=2.0,
        risk_per_trade=0.005,
        max_daily_trades=12,
        max_hold_bars_scalping=20,
        max_hold_bars_swing=120,
        cooling_off_streak=3,
        session_weights={"TOKYO": 0.3, "LONDON": 1.0, "NEW_YORK": 0.8, "OFF_HOURS": 0.2},
        quality_score_threshold=58.0,
        conviction_min=0.52,
        min_mtf_agreement=2,
        signal_cooldown_sec=45,
        cross_asset_correlators=("DXY",),
        cross_asset_inverse={"DXY": True},
        tick_imbalance_window=20,
        tick_imbalance_signal=0.60,
        atr_period=14,
        min_atr=0.0005,
        high_vol_multiplier=1.2,
        display_name="Euro / USD",
        quote_currency="USD",
        price_format=".5f",
    ),

    "BTCUSD": AssetProfile(
        symbol="BTCUSD",
        pip_value=1.0,
        digits=1,
        contract_size=1.0,
        asset_class="crypto",
        max_spread_pips=50.0,
        sl_atr_multiplier=1.5,
        tp_atr_multiplier=3.0,
        min_rr_ratio=2.0,
        risk_per_trade=0.005,
        max_daily_trades=6,
        max_hold_bars_scalping=15,
        max_hold_bars_swing=96,
        cooling_off_streak=2,
        session_weights={"TOKYO": 0.7, "LONDON": 0.8, "NEW_YORK": 1.0, "OFF_HOURS": 0.6},
        quality_score_threshold=65.0,
        conviction_min=0.56,
        min_mtf_agreement=2,
        signal_cooldown_sec=90,
        cross_asset_correlators=(),
        cross_asset_inverse={},
        tick_imbalance_window=15,
        tick_imbalance_signal=0.65,
        atr_period=14,
        min_atr=100.0,
        high_vol_multiplier=1.5,
        display_name="Bitcoin / USD",
        quote_currency="USD",
        price_format=".1f",
    ),

    "US500": AssetProfile(
        symbol="US500",
        pip_value=0.1,
        digits=1,
        contract_size=1.0,
        asset_class="index",
        max_spread_pips=5.0,
        sl_atr_multiplier=1.3,
        tp_atr_multiplier=2.5,
        min_rr_ratio=2.0,
        risk_per_trade=0.005,
        max_daily_trades=8,
        max_hold_bars_scalping=15,
        max_hold_bars_swing=96,
        cooling_off_streak=3,
        session_weights={"TOKYO": 0.3, "LONDON": 0.7, "NEW_YORK": 1.0, "OFF_HOURS": 0.1},
        quality_score_threshold=60.0,
        conviction_min=0.53,
        min_mtf_agreement=2,
        signal_cooldown_sec=60,
        cross_asset_correlators=("US10Y",),
        cross_asset_inverse={"US10Y": True},
        tick_imbalance_window=20,
        tick_imbalance_signal=0.62,
        atr_period=14,
        min_atr=2.0,
        high_vol_multiplier=1.3,
        display_name="S&P 500",
        quote_currency="USD",
        price_format=".1f",
    ),
}

# Aliases
_PROFILES["GOLD"] = _PROFILES["XAUUSD"]
_PROFILES["SP500"] = _PROFILES["US500"]
_PROFILES["BTCUSDT"] = _PROFILES["BTCUSD"]


def _normalize(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace(".", "").replace(" ", "")


@lru_cache(maxsize=32)
def get_profile(symbol: str) -> AssetProfile:
    """Return the AssetProfile for a symbol, falling back to a generic forex profile."""
    s = _normalize(symbol)
    if s in _PROFILES:
        return _PROFILES[s]
    # Infer generic profile
    if "JPY" in s:
        return AssetProfile(symbol=s, pip_value=0.01, digits=3, asset_class="forex",
                            display_name=s)
    if "XAU" in s or "GOLD" in s:
        return _PROFILES["XAUUSD"]
    if any(c in s for c in ("BTC", "ETH", "SOL")):
        return _PROFILES["BTCUSD"]
    if any(i in s for i in ("US500", "US30", "USTEC", "GER", "NAS")):
        return _PROFILES["US500"]
    return AssetProfile(symbol=s, display_name=s)


def profile_to_dict(p: AssetProfile) -> dict[str, Any]:
    """Serialize profile to a plain dict (for API/Redis)."""
    return {
        "symbol": p.symbol,
        "asset_class": p.asset_class,
        "pip_value": p.pip_value,
        "max_spread_pips": p.max_spread_pips,
        "sl_atr_multiplier": p.sl_atr_multiplier,
        "tp_atr_multiplier": p.tp_atr_multiplier,
        "min_rr_ratio": p.min_rr_ratio,
        "risk_per_trade": p.risk_per_trade,
        "max_daily_trades": p.max_daily_trades,
        "quality_score_threshold": p.quality_score_threshold,
        "conviction_min": p.conviction_min,
        "min_mtf_agreement": p.min_mtf_agreement,
        "signal_cooldown_sec": p.signal_cooldown_sec,
        "cross_asset_correlators": list(p.cross_asset_correlators),
        "cross_asset_inverse": dict(p.cross_asset_inverse),
        "session_weights": dict(p.session_weights),
        "display_name": p.display_name or p.symbol,
        "price_format": p.price_format,
    }
