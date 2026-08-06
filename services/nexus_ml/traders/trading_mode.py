"""
Trading Mode Configuration — Central switch between SCALPING and SWING.

Controls all mode-dependent parameters: timeframes, SL/TP ranges,
hold times, retraining intervals, session rules, API budgets,
lot sizing, and multi-timeframe confirmation.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class TradingMode(str, Enum):
    SCALPING = "scalping"
    SWING = "swing"


class Session(str, Enum):
    SYDNEY = "SYDNEY"
    TOKYO = "TOKYO"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"
    OVERLAP = "OVERLAP"
    OFF_HOURS = "OFF_HOURS"


# Delegate to shared unified session module
from shared.sessions import detect_session as _detect_session  # noqa: E402


def detect_session(utc_hour: int) -> Session:
    """Detect session via shared.sessions (single source of truth)."""
    return Session(_detect_session(utc_hour).value)


# ── Session Multipliers ─────────────────────────────────────────────────────

@dataclass
class SessionConfig:
    """Per-session adjustments for risk and sizing."""
    liquidity_factor: float       # 0.0-1.0, higher = more liquid
    spread_tolerance: float       # max spread multiplier vs average
    size_multiplier: float        # lot size multiplier
    max_trades: int               # max trades per session
    min_conviction: float         # min confidence to trade
    scalping_allowed: bool        # can we scalp in this session?


SESSION_CONFIGS: dict[TradingMode, dict[Session, SessionConfig]] = {
    TradingMode.SCALPING: {
        Session.OVERLAP:  SessionConfig(1.0, 1.3, 1.25, 15, 0.50, True),
        Session.LONDON:   SessionConfig(0.9, 1.5, 1.10, 12, 0.52, True),
        Session.NEW_YORK: SessionConfig(0.85, 1.5, 1.00, 10, 0.52, True),
        # TOKYO WR=22% in prod → Kelly=-0.30 → raise min_conviction to 0.85, cap at 2 trades
        Session.TOKYO:    SessionConfig(0.6, 1.8, 0.35, 2, 0.85, True),
        Session.SYDNEY:   SessionConfig(0.3, 2.0, 0.40, 3, 0.65, False),
        Session.OFF_HOURS:SessionConfig(0.0, 0.0, 0.00, 0, 1.00, False),
    },
    TradingMode.SWING: {
        Session.OVERLAP:  SessionConfig(1.0, 2.0, 1.20, 5, 0.55, True),
        Session.LONDON:   SessionConfig(0.9, 2.0, 1.10, 5, 0.55, True),
        Session.NEW_YORK: SessionConfig(0.85, 2.0, 1.00, 4, 0.55, True),
        # TOKYO WR=22% in prod → Kelly=-0.30 → raise min_conviction to 0.85, cap at 1 trade
        Session.TOKYO:    SessionConfig(0.6, 2.5, 0.40, 1, 0.85, True),
        Session.SYDNEY:   SessionConfig(0.3, 3.0, 0.50, 2, 0.65, True),
        Session.OFF_HOURS:SessionConfig(0.0, 0.0, 0.00, 0, 1.00, False),
    },
}


# ── Mode Configuration ──────────────────────────────────────────────────────

@dataclass
class TradingModeConfig:
    """Complete configuration for a trading mode."""
    mode: TradingMode

    # ── Timeframes ──
    operational_tf: str              # primary decision timeframe
    confirmation_tfs: list[str]      # higher TFs for MTF confirmation
    min_mtf_agreement: int           # min number of HTFs that must agree

    # ── SL/TP Ranges (pips) ──
    sl_min: float
    sl_max: float
    tp_min: float
    tp_max: float
    sl_atr_multiplier: float         # SL = ATR * this
    tp_atr_multiplier: float         # TP = ATR * this

    # ── Hold Time ──
    max_hold_bars: int               # max bars before forced exit
    min_hold_bars: int               # min bars before allowing exit (avoid noise)

    # ── Model Parameters ──
    ml_confidence_threshold: float
    dl_seq_len: int
    dl_confidence_threshold: float
    rl_confidence_threshold: float
    ensemble_weights: dict[str, float]

    # ── Retraining ──
    retrain_interval_sec: int        # auto-retrain every N seconds
    retrain_lookback_days: int       # training data window

    # ── GenAI API Budget ──
    genai_max_calls_per_hour: int    # rate limit
    genai_cooldown_sec: int          # min seconds between calls
    genai_enabled: bool              # can disable GenAI entirely

    # ── Backtester ──
    bt_spread_pips: float
    bt_trailing_trigger_rr: float
    bt_trailing_offset_pips: float

    # ── Risk ──
    risk_per_trade: float            # fraction of balance
    max_daily_trades: int
    max_open_positions: int
    max_daily_loss_pct: float
    max_weekly_dd_pct: float
    min_rr_ratio: float

    # ── Spread Filter ──
    pre_signal_spread_filter: bool   # reject signals if spread too wide
    max_spread_pips: float           # absolute max spread for entry


def get_mode_config(mode: TradingMode) -> TradingModeConfig:
    """Get the complete configuration for a trading mode."""
    if mode == TradingMode.SCALPING:
        return TradingModeConfig(
            mode=TradingMode.SCALPING,
            # Timeframes
            operational_tf="M5",
            confirmation_tfs=["M15", "H1"],
            min_mtf_agreement=1,       # at least 1 HTF must agree
            # SL/TP — widened for gold: ATR-based floor (Fix #3) handles the
            # minimum; these ranges are the hard clamp. Old sl_max=15 was only
            # $0.15 for gold (pip=0.01), now 2500 pips = $25 — still tight vs
            # gold's $30-60 hourly range but the ATR floor overrides anyway.
            sl_min=50.0,   sl_max=2500.0,
            tp_min=80.0,   tp_max=5000.0,
            sl_atr_multiplier=1.5,
            tp_atr_multiplier=4.0,     # wider TP: aim for 4× ATR reward
            # Hold time
            max_hold_bars=12,          # 12 bars * 5min = 60min max
            min_hold_bars=1,           # at least 5 min (front-configurable)
            # Models
            ml_confidence_threshold=0.55,
            dl_seq_len=10,             # 10 bars = 50min lookback (sharper for M5)
            dl_confidence_threshold=0.40,
            rl_confidence_threshold=0.42,
            ensemble_weights={"ml": 0.40, "dl": 0.30, "rl": 0.30},
            # Retraining
            retrain_interval_sec=4 * 3600,   # every 4 hours
            retrain_lookback_days=14,         # 2 weeks of data
            # GenAI budget
            genai_max_calls_per_hour=10,      # conservative for scalping
            genai_cooldown_sec=300,            # 5 min between calls
            genai_enabled=True,
            # Backtester
            bt_spread_pips=1.5,
            bt_trailing_trigger_rr=1.2,
            bt_trailing_offset_pips=5.0,
            # Risk
            risk_per_trade=0.01,       # 1% per trade (tighter for scalping)
            max_daily_trades=20,
            max_open_positions=3,
            max_daily_loss_pct=0.03,   # 3% daily max loss
            max_weekly_dd_pct=0.06,    # 6% weekly max DD
            min_rr_ratio=2.0,          # require TP >= 2× SL for viable expectancy
            # Spread
            pre_signal_spread_filter=True,
            max_spread_pips=2.5,
        )
    else:  # SWING
        return TradingModeConfig(
            mode=TradingMode.SWING,
            # Timeframes
            operational_tf="M15",
            confirmation_tfs=["H1", "H4"],
            min_mtf_agreement=1,
            # SL/TP
            sl_min=10.0,  sl_max=50.0,
            tp_min=20.0,  tp_max=150.0,
            sl_atr_multiplier=1.5,
            tp_atr_multiplier=2.5,
            # Hold time
            max_hold_bars=120,         # 120 bars * 15min = 30h max
            min_hold_bars=4,           # at least 1h
            # Models
            ml_confidence_threshold=0.55,
            dl_seq_len=30,
            dl_confidence_threshold=0.50,
            rl_confidence_threshold=0.45,
            ensemble_weights={"ml": 0.40, "dl": 0.35, "rl": 0.25},
            # Retraining
            retrain_interval_sec=24 * 3600,  # daily
            retrain_lookback_days=60,         # 2 months
            # GenAI budget
            genai_max_calls_per_hour=30,
            genai_cooldown_sec=60,
            genai_enabled=True,
            # Backtester
            bt_spread_pips=1.5,
            bt_trailing_trigger_rr=1.5,
            bt_trailing_offset_pips=10.0,
            # Risk
            risk_per_trade=0.02,       # 2% per trade
            max_daily_trades=10,
            max_open_positions=3,
            max_daily_loss_pct=0.05,
            max_weekly_dd_pct=0.08,
            min_rr_ratio=1.5,
            # Spread
            pre_signal_spread_filter=True,
            max_spread_pips=3.5,
        )


# ── Dynamic SL/TP Calculator ───────────────────────────────────────────────


def compute_dynamic_sl_tp(
    atr: float,
    pip_value: float,
    config: TradingModeConfig,
    session: Session,
    volatility_regime: str = "MEDIUM",
) -> tuple[float, float]:
    """Calculate dynamic SL/TP based on ATR, session, and volatility.

    Returns (sl_pips, tp_pips) clamped to mode limits.
    """
    atr_pips = atr / pip_value if pip_value > 0 else 15.0

    # Base SL/TP from ATR
    sl_pips = atr_pips * config.sl_atr_multiplier
    tp_pips = atr_pips * config.tp_atr_multiplier

    # Volatility regime adjustment
    vol_multipliers = {
        "LOW": (0.8, 1.2),      # tighter SL, wider TP ratio
        "MEDIUM": (1.0, 1.0),
        "HIGH": (1.3, 0.9),     # wider SL, slightly tighter TP ratio
        "EXTREME": (1.5, 0.7),  # much wider SL, tighter TP
    }
    sl_mult, tp_mult = vol_multipliers.get(volatility_regime, (1.0, 1.0))
    sl_pips *= sl_mult
    tp_pips *= tp_mult

    # Session adjustment (tighter in low-liquidity sessions)
    sess_configs = SESSION_CONFIGS.get(config.mode, {})
    sess_cfg = sess_configs.get(session)
    if sess_cfg:
        # In low-liquidity sessions, widen SL to avoid noise stops
        if sess_cfg.liquidity_factor < 0.5:
            sl_pips *= 1.2
            tp_pips *= 1.1

    # Clamp to mode limits
    sl_pips = max(config.sl_min, min(sl_pips, config.sl_max))
    tp_pips = max(config.tp_min, min(tp_pips, config.tp_max))

    # Ensure minimum R:R
    if sl_pips > 0 and tp_pips / sl_pips < config.min_rr_ratio:
        tp_pips = sl_pips * config.min_rr_ratio

    return round(sl_pips, 1), round(tp_pips, 1)


# ── Dynamic Lot Sizing ─────────────────────────────────────────────────────


def compute_dynamic_lots(
    balance: float,
    sl_pips: float,
    config: TradingModeConfig,
    session: Session,
    pip_value_per_lot: float = 10.0,
    confidence: float = 1.0,
    symbol: str = "XAUUSD",
) -> float:
    """Calculate position size based on balance, risk, SL, session, and confidence."""
    if sl_pips <= 0 or balance <= 0:
        return 0.01

    try:
        from shared.asset_specs import get_pip_value_per_lot
        pip_value_per_lot = get_pip_value_per_lot(symbol)
    except Exception:
        pass

    risk_amount = balance * config.risk_per_trade

    sess_configs = SESSION_CONFIGS.get(config.mode, {})
    sess_cfg = sess_configs.get(session)
    size_mult = sess_cfg.size_multiplier if sess_cfg else 1.0

    conf_factor = 0.6 + 0.4 * min(1.0, max(0.0, confidence))

    risk_per_pip = risk_amount / sl_pips
    lots = risk_per_pip / pip_value_per_lot

    # Apply multipliers
    lots *= size_mult * conf_factor

    # Clamp
    lots = max(0.01, min(lots, 5.0))

    return round(lots, 2)


# ── GenAI API Rate Limiter ──────────────────────────────────────────────────


class GenAIRateLimiter:
    """Controls GenAI API call frequency to manage costs."""

    def __init__(self, config: TradingModeConfig) -> None:
        self.max_per_hour = config.genai_max_calls_per_hour
        self.cooldown_sec = config.genai_cooldown_sec
        self.enabled = config.genai_enabled
        self._call_timestamps: list[float] = []
        self._last_call: float = 0.0

    def can_call(self) -> bool:
        """Check if a GenAI API call is allowed right now."""
        if not self.enabled:
            return False

        now = time.time()

        # Cooldown check
        if now - self._last_call < self.cooldown_sec:
            return False

        # Hourly rate check
        one_hour_ago = now - 3600
        self._call_timestamps = [t for t in self._call_timestamps if t > one_hour_ago]

        if len(self._call_timestamps) >= self.max_per_hour:
            return False

        return True

    def record_call(self) -> None:
        """Record that a GenAI API call was made."""
        now = time.time()
        self._call_timestamps.append(now)
        self._last_call = now

    @property
    def calls_remaining(self) -> int:
        now = time.time()
        one_hour_ago = now - 3600
        recent = len([t for t in self._call_timestamps if t > one_hour_ago])
        return max(0, self.max_per_hour - recent)

    @property
    def next_available_in(self) -> float:
        """Seconds until next call is allowed. 0 if available now."""
        if self.can_call():
            return 0.0
        now = time.time()
        cooldown_remaining = max(0, self.cooldown_sec - (now - self._last_call))
        return cooldown_remaining


# ── Multi-Timeframe Confirmer ───────────────────────────────────────────────


def mtf_confirm_signal(
    signal_direction: str,
    htf_trends: dict[str, str],
    config: TradingModeConfig,
) -> tuple[bool, str]:
    """Confirm a signal against higher-timeframe trends.

    Parameters
    ----------
    signal_direction : "BUY" or "SELL"
    htf_trends : {"M15": "BULLISH", "H1": "BEARISH", ...} per confirmation TF
    config : current trading mode config

    Returns
    -------
    (confirmed, reasoning)
    """
    if signal_direction == "NO_TRADE":
        return True, "NO_TRADE needs no confirmation"

    expected_trend = "BULLISH" if signal_direction == "BUY" else "BEARISH"

    agreeing = 0
    details = []
    for tf in config.confirmation_tfs:
        trend = htf_trends.get(tf, "NEUTRAL")
        if trend == expected_trend:
            agreeing += 1
            details.append(f"{tf}={trend} AGREE")
        elif trend == "NEUTRAL":
            details.append(f"{tf}={trend} NEUTRAL")
        else:
            details.append(f"{tf}={trend} DISAGREE")

    confirmed = agreeing >= config.min_mtf_agreement
    reasoning = f"MTF confirmation: {agreeing}/{config.min_mtf_agreement} needed. " + ", ".join(details)

    return confirmed, reasoning


# ── Spread Pre-Filter ───────────────────────────────────────────────────────


def spread_filter_ok(
    current_spread_pips: float,
    avg_spread_pips: float,
    config: TradingModeConfig,
    session: Session,
) -> tuple[bool, str]:
    """Check if current spread is acceptable for trading.

    Returns (ok, reason).
    """
    if not config.pre_signal_spread_filter:
        return True, "Spread filter disabled"

    sess_configs = SESSION_CONFIGS.get(config.mode, {})
    sess_cfg = sess_configs.get(session)
    tolerance = sess_cfg.spread_tolerance if sess_cfg else 2.0

    max_allowed = min(
        avg_spread_pips * tolerance,
        config.max_spread_pips,
    )

    if current_spread_pips > max_allowed:
        return False, (
            f"Spread {current_spread_pips:.1f} > max {max_allowed:.1f} pips "
            f"(avg={avg_spread_pips:.1f}, tolerance={tolerance}x, session={session.value})"
        )

    return True, f"Spread OK: {current_spread_pips:.1f} <= {max_allowed:.1f}"


# ── Dynamic Regime Policy ───────────────────────────────────────────────────
# Adapts the operating metrics (conviction gate, R:R floor, size, and even
# whether to trade at all) to the CURRENT market regime, so the system trades
# differently in a trend vs a range vs a breakout vs a dead market.


@dataclass
class RegimePolicy:
    allow_trade: bool
    min_conviction: float       # 0..1 conviction gate for this regime
    min_rr: float               # required reward:risk
    size_mult: float            # lot-size multiplier
    mean_reversion_only: bool   # in ranges: only fade extremes
    require_trend_align: bool   # in trends: only trade WITH the trend
    reason: str = ""


def get_regime_policy(
    regime: str,
    *,
    base_min_conviction: float,
    base_min_rr: float = 1.5,
) -> RegimePolicy:
    """Return regime-specific operating rules layered on top of the base gates."""
    r = (regime or "UNKNOWN").upper()

    if r == "TRENDING":
        # Trend: trade WITH the trend, standard conviction, solid R:R.
        return RegimePolicy(
            allow_trade=True,
            min_conviction=base_min_conviction,
            min_rr=max(base_min_rr, 1.5),
            size_mult=1.0,
            mean_reversion_only=False,
            require_trend_align=True,
            reason="TRENDING: trade with trend",
        )
    if r == "BREAKOUT":
        # Breakout: needs momentum; demand higher R:R, slightly higher bar.
        return RegimePolicy(
            allow_trade=True,
            min_conviction=min(0.95, base_min_conviction + 0.05),
            min_rr=max(base_min_rr, 2.0),
            size_mult=0.9,
            mean_reversion_only=False,
            require_trend_align=False,
            reason="BREAKOUT: momentum + high R:R",
        )
    if r == "RANGING":
        # Range: ONLY mean-reversion at the extremes, higher conviction, smaller.
        return RegimePolicy(
            allow_trade=True,
            min_conviction=min(0.95, base_min_conviction + 0.07),
            min_rr=max(base_min_rr, 1.5),
            size_mult=0.8,
            mean_reversion_only=True,
            require_trend_align=False,
            reason="RANGING: fade extremes only",
        )
    # Unknown → conservative.
    return RegimePolicy(
        allow_trade=True,
        min_conviction=min(0.95, base_min_conviction + 0.05),
        min_rr=max(base_min_rr, 1.5),
        size_mult=0.8,
        mean_reversion_only=False,
        require_trend_align=False,
        reason="UNKNOWN regime: conservative",
    )


def classify_trend_regime(features: dict[str, float]) -> str:
    """Multi-signal regime classifier. Uses ADX, BB, RSI, EMA cross, and
    nexus-features' own trend classification for a robust verdict.

    Order: BREAKOUT → TRENDING → RANGING (not the other way around).
    Defaults to TRENDING when evidence is mixed — better to trade a weak
    trend than to sit out a trending move labelled as RANGING.
    """
    adx = float(features.get("adx", 0) or 0)
    bb_width = float(features.get("bb_width", features.get("bb_bandwidth", 0)) or 0)
    atr_norm = float(features.get("atr_normalized", 0) or 0)
    rsi = float(features.get("rsi", features.get("rsi_14", 50)) or 50)

    # Features from nexus-features trend engine
    trend_dir = str(features.get("trend_direction", "") or "").upper()
    trend_str = float(features.get("trend_strength", 0) or 0)

    # EMA alignment (9 > 21 > 50 = bullish, opposite = bearish)
    ema9 = float(features.get("ema_9", 0) or 0)
    ema21 = float(features.get("ema_21", 0) or 0)
    ema50 = float(features.get("ema_50", 0) or 0)
    ema_trending = False
    if ema9 > 0 and ema21 > 0 and ema50 > 0:
        ema_trending = (ema9 > ema21 > ema50) or (ema9 < ema21 < ema50)

    # 1. Breakout: Bollinger squeeze + ATR spike
    is_breakout = bool(bb_width and bb_width < 0.5 and atr_norm > 1.2)
    if is_breakout:
        return "BREAKOUT"

    # 2. Trending signals: any 2 of the following qualify
    trending_signals = 0
    if adx >= 22:          trending_signals += 1  # lowered from 25 → less miss
    if rsi > 65 or rsi < 35:  trending_signals += 1
    if ema_trending:       trending_signals += 1
    if trend_str > 0.5:    trending_signals += 1
    if trend_dir in ("BULLISH", "BEARISH", "UP", "DOWN", "STRONG_UP", "STRONG_DOWN"):
        trending_signals += 1

    if trending_signals >= 2:
        return "TRENDING"

    # 3. Clear ranging: tight ADX, middle RSI, no EMA stack
    if adx > 0 and adx < 18 and 40 < rsi < 60 and not ema_trending:
        return "RANGING"

    # 4. Default: TRENDING (favour action over paralysis)
    return "TRENDING"


def market_quality_gate(
    features: dict[str, float],
    *,
    current_spread: float,
    avg_spread: float,
) -> tuple[bool, str]:
    """Reject low-quality conditions: dead/illiquid market or blown spread.

    We avoid gating purely on tick-volume (many brokers don't stream it for
    gold). Instead we detect a *dead* market via near-zero volatility (ATR) and
    an abnormally wide spread relative to the move on offer.
    """
    atr = float(features.get("atr_14", features.get("atr", 0)) or 0)
    atr_sma = float(features.get("atr_sma", features.get("atr_20_sma", atr)) or atr)

    # Dead market: ATR collapsed far below its own average → no real movement,
    # stops get picked off by noise. Skip.
    if atr_sma > 0 and atr > 0 and (atr / atr_sma) < 0.35:
        return False, f"dead market: ATR {atr:.2f} << avg {atr_sma:.2f}"

    # Spread eating the edge: if the spread is a large fraction of the ATR, the
    # expected move barely covers transaction cost → skip.
    if atr > 0 and current_spread > 0 and (current_spread / atr) > 0.4:
        return False, f"spread {current_spread:.2f} too large vs ATR {atr:.2f}"

    return True, "ok"


# ── Dynamic Timeframe Selection ─────────────────────────────────────────────
# Picks M1 / M5 / M15 automatically from session, volatility, and spread.
# Scalping only — swing mode keeps the fixed config TF.


@dataclass(frozen=True)
class DynamicTFSelection:
    operational_tf: str
    confirmation_tfs: list[str]
    base_tf: str
    reason: str
    vol_regime: str
    session: str
    auto_selected: bool


_HTF_BY_OPERATIONAL: dict[str, list[str]] = {
    "M1":  ["M5", "M15", "H1"],
    "M5":  ["M15", "H1", "H4"],
    "M15": ["H1", "H4", "D1"],
    "M30": ["H1", "H4", "D1"],
    "H1":  ["H4", "D1"],
}


def get_confirmation_tfs_for(operational_tf: str, mode: TradingMode) -> list[str]:
    """Higher TFs used to confirm entries on the operational TF."""
    if mode == TradingMode.SWING:
        return ["H1", "H4"]
    return list(_HTF_BY_OPERATIONAL.get(operational_tf.upper(), ["M15", "H1"]))


def detect_vol_regime_from_features(features: dict[str, Any]) -> str:
    """Classify volatility from feature dict (string label or ATR ratio)."""
    vol_str = str(features.get("vol_regime", "") or "").upper()
    if vol_str in ("LOW", "MEDIUM", "HIGH", "EXTREME"):
        return vol_str

    atr = float(features.get("atr_14", features.get("atr", 0)) or 0)
    atr_sma = float(features.get("atr_sma", features.get("atr_20_sma", atr)) or atr)
    if atr_sma <= 0:
        return "MEDIUM"
    ratio = atr / atr_sma
    if ratio > 1.8:
        return "EXTREME"
    if ratio > 1.3:
        return "HIGH"
    if ratio < 0.6:
        return "LOW"
    return "MEDIUM"


def select_operational_tf(
    config: TradingModeConfig,
    session: Session,
    features: dict[str, Any],
    *,
    auto: bool = True,
    current_spread: float = 0.0,
    avg_spread: float = 0.0,
) -> DynamicTFSelection:
    """Choose the best operational TF for current market conditions.

    Rules (scalping + auto on):
      - EXTREME/HIGH vol  → M5  (less noise than M1, ATR stops)
      - OVERLAP + calm vol + tight spread → M1  (ultra-scalp window)
      - London/NY/overlap medium vol → M5
      - Thin sessions (Sydney/Tokyo) → M5, never M1
      - Dead/low-liquidity calm → M15 patience
    """
    vol_regime = detect_vol_regime_from_features(features)
    base = config.operational_tf
    default_confirm = get_confirmation_tfs_for(base, config.mode)

    if not auto or config.mode != TradingMode.SCALPING:
        return DynamicTFSelection(
            operational_tf=base,
            confirmation_tfs=default_confirm,
            base_tf=base,
            reason="fixed TF (swing mode or auto_tf off)",
            vol_regime=vol_regime,
            session=session.value,
            auto_selected=False,
        )

    sess_cfgs = SESSION_CONFIGS.get(config.mode, {})
    sess_cfg = sess_cfgs.get(session)
    liquidity = sess_cfg.liquidity_factor if sess_cfg else 0.5
    spread_tol = sess_cfg.spread_tolerance if sess_cfg else 2.0
    spread_ratio = (current_spread / avg_spread) if avg_spread > 0 else 1.0
    spread_tight = spread_ratio <= max(1.0, spread_tol * 0.75)

    selected = "M5"
    reason = "default M5 scalping"

    if vol_regime == "EXTREME":
        selected, reason = "M5", "EXTREME vol → M5 (wider ATR stops, less M1 noise)"
    elif vol_regime == "HIGH":
        selected, reason = "M5", "HIGH vol → M5"
    elif (
        session == Session.OVERLAP
        and vol_regime in ("LOW", "MEDIUM")
        and liquidity >= 0.9
        and spread_tight
    ):
        selected, reason = "M1", "OVERLAP + calm vol + tight spread → M1 ultra-scalp"
    elif session in (Session.LONDON, Session.NEW_YORK, Session.OVERLAP):
        selected, reason = "M5", f"{session.value} active session → M5"
    elif session in (Session.SYDNEY, Session.TOKYO) or liquidity < 0.7:
        selected, reason = "M5", f"{session.value} thin liquidity → M5 (no M1)"
    elif vol_regime == "LOW" and liquidity < 0.55:
        selected, reason = "M15", "LOW vol + off-peak → M15 patience mode"

    confirmation_tfs = get_confirmation_tfs_for(selected, config.mode)

    return DynamicTFSelection(
        operational_tf=selected,
        confirmation_tfs=confirmation_tfs,
        base_tf=base,
        reason=reason,
        vol_regime=vol_regime,
        session=session.value,
        auto_selected=True,
    )


# ── Global Mode State ──────────────────────────────────────────────────────

_current_mode: TradingMode = TradingMode(
    os.getenv("NEXUS_TRADING_MODE_TYPE", "scalping").lower()
)
_current_config: TradingModeConfig = get_mode_config(_current_mode)
_rate_limiter: GenAIRateLimiter = GenAIRateLimiter(_current_config)


def get_current_mode() -> TradingMode:
    return _current_mode


def get_current_config() -> TradingModeConfig:
    return _current_config


def get_rate_limiter() -> GenAIRateLimiter:
    return _rate_limiter


def switch_mode(new_mode: TradingMode) -> TradingModeConfig:
    """Switch trading mode globally. Returns the new config."""
    global _current_mode, _current_config, _rate_limiter
    _current_mode = new_mode
    _current_config = get_mode_config(new_mode)
    _rate_limiter = GenAIRateLimiter(_current_config)
    logger.info("Trading mode switched to: %s", new_mode.value)
    return _current_config
