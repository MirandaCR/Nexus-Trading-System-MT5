"""
Rule-Based Signal Generator — fallback when ML models are not trained.

Uses real technical indicators computed by nexus-features (RSI, EMA, MACD,
ADX, Bollinger Bands, Stochastic) to generate directional trading signals.

This is NOT mock/synthetic data — it processes the exact same features
that the ML ensemble would consume, just using deterministic rules instead
of trained model weights.

The output is an EnsembleSignal identical to what ModelEnsemble.predict()
returns, so the rest of the pipeline (nexus-agents, risk, executor) works
unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .ensemble import EnsembleSignal

logger = logging.getLogger(__name__)

# ── Indicator key names (must match nexus-features output) ───────────────────

_K = {
    "rsi": "rsi",
    "ema_9": "ema_9",
    "ema_21": "ema_21",
    "ema_50": "ema_50",
    "ema_200": "ema_200",
    "macd": "macd",
    "macd_histogram": "macd_histogram",
    "macd_signal": "macd_signal",
    "bb_upper": "bb_upper",
    "bb_lower": "bb_lower",
    "bb_mid": "bb_mid",
    "bb_pct_b": "bb_pct_b",
    "bb_bandwidth": "bb_bandwidth",
    "stoch_k": "stoch_k",
    "stoch_d": "stoch_d",
    "adx": "adx",
    "di_plus": "di_plus",
    "di_minus": "di_minus",
    "atr": "atr",
    "atr_normalized": "atr_normalized",
    "trend_direction": "trend_direction",
    "trend_strength": "trend_strength",
    "trend_score": "trend_score",
    "vol_regime": "vol_regime",
}


def _get(features: dict, key: str, default: float = 0.0) -> float:
    """Safely extract a numeric feature value."""
    val = features.get(key, default)
    if isinstance(val, str):
        # trend_direction, vol_regime are strings — skip
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _get_str(features: dict, key: str, default: str = "") -> str:
    """Safely extract a string feature value."""
    val = features.get(key, default)
    return str(val) if val is not None else default


class RuleBasedSignalGenerator:
    """
    Multi-factor rule-based signal generator.

    Scoring system (each factor votes ±1 with a weight):
      - EMA trend alignment  (weight 0.25)
      - RSI momentum         (weight 0.20)
      - MACD momentum        (weight 0.20)
      - ADX trend strength   (weight 0.15)
      - Bollinger position   (weight 0.10)
      - Stochastic crossover (weight 0.10)

    Final confidence = weighted score magnitude ∈ [0, 1].
    Direction = BUY if score > 0, SELL if score < 0, NO_TRADE if |score| < threshold.
    """

    def __init__(self, confidence_threshold: float = 0.35) -> None:
        self.confidence_threshold = confidence_threshold
        self.weights = {
            "ema_trend": 0.25,
            "rsi": 0.20,
            "macd": 0.20,
            "adx": 0.15,
            "bollinger": 0.10,
            "stochastic": 0.10,
            "tick_flow": 0.15,   # order-flow veto: beats trend noise
        }

    def predict(self, features: dict[str, float | str]) -> EnsembleSignal:
        """Generate a signal from real indicator values.

        Parameters
        ----------
        features : dict with indicator names as keys (from nexus-features).
        """
        scores: dict[str, float] = {}
        triggered: list[str] = []

        # ── 1. EMA Trend Alignment ──────────────────────────────────────
        ema_9 = _get(features, "ema_9")
        ema_21 = _get(features, "ema_21")
        ema_50 = _get(features, "ema_50")
        ema_200 = _get(features, "ema_200")

        if ema_9 > 0 and ema_21 > 0:
            ema_score = 0.0
            if ema_9 > ema_21:
                ema_score += 0.4
            else:
                ema_score -= 0.4
            if ema_21 > ema_50 > 0:
                ema_score += 0.3
            elif ema_50 > ema_21 > 0:
                ema_score -= 0.3
            if ema_50 > ema_200 > 0:
                ema_score += 0.3
            elif ema_200 > ema_50 > 0:
                ema_score -= 0.3
            scores["ema_trend"] = max(-1.0, min(1.0, ema_score))
            triggered.append("ema_trend")

        # ── 2. RSI Momentum ─────────────────────────────────────────────
        rsi = _get(features, "rsi")
        if rsi > 0:
            if rsi > 70:
                # Overbought → bearish signal (possible reversal)
                scores["rsi"] = -0.8
            elif rsi > 60:
                # Bullish momentum
                scores["rsi"] = 0.6
            elif rsi > 50:
                # Mild bullish
                scores["rsi"] = 0.3
            elif rsi > 40:
                # Mild bearish
                scores["rsi"] = -0.3
            elif rsi > 30:
                # Bearish momentum
                scores["rsi"] = -0.6
            else:
                # Oversold → bullish signal (possible reversal)
                scores["rsi"] = 0.8
            triggered.append("rsi")

        # ── 3. MACD Momentum ────────────────────────────────────────────
        macd_hist = _get(features, "macd_histogram")
        macd_val = _get(features, "macd")
        macd_signal = _get(features, "macd_signal")

        if macd_val != 0 or macd_hist != 0:
            macd_score = 0.0
            # Histogram direction (most responsive)
            if macd_hist > 0:
                macd_score += 0.5
            elif macd_hist < 0:
                macd_score -= 0.5
            # MACD above/below signal line
            if macd_val > macd_signal:
                macd_score += 0.3
            elif macd_val < macd_signal:
                macd_score -= 0.3
            # MACD above/below zero
            if macd_val > 0:
                macd_score += 0.2
            elif macd_val < 0:
                macd_score -= 0.2
            scores["macd"] = max(-1.0, min(1.0, macd_score))
            triggered.append("macd")

        # ── 4. ADX Trend Strength ───────────────────────────────────────
        adx = _get(features, "adx")
        di_plus = _get(features, "di_plus")
        di_minus = _get(features, "di_minus")

        if adx > 0:
            if adx >= 25:
                # Strong trend — direction from DI
                if di_plus > di_minus:
                    scores["adx"] = min(1.0, adx / 50)
                elif di_minus > di_plus:
                    scores["adx"] = -min(1.0, adx / 50)
                else:
                    scores["adx"] = 0.0
            else:
                # Weak trend — low confidence, slight directional bias
                if di_plus > di_minus:
                    scores["adx"] = 0.15
                elif di_minus > di_plus:
                    scores["adx"] = -0.15
                else:
                    scores["adx"] = 0.0
            triggered.append("adx")

        # ── 5. Bollinger Band Position ──────────────────────────────────
        bb_pct_b = _get(features, "bb_pct_b")
        bb_bandwidth = _get(features, "bb_bandwidth")

        if bb_pct_b != 0 or bb_bandwidth > 0:
            if bb_pct_b > 1.0:
                # Above upper band → overbought/momentum breakout
                scores["bollinger"] = -0.5 if bb_bandwidth < 0.02 else 0.6
            elif bb_pct_b > 0.8:
                scores["bollinger"] = 0.4
            elif bb_pct_b > 0.5:
                scores["bollinger"] = 0.2
            elif bb_pct_b > 0.2:
                scores["bollinger"] = -0.2
            elif bb_pct_b > 0.0:
                scores["bollinger"] = -0.4
            else:
                # Below lower band → oversold/breakdown
                scores["bollinger"] = 0.5 if bb_bandwidth < 0.02 else -0.6
            triggered.append("bollinger")

        # ── 6. Tick Imbalance (order flow) ─────────────────────────────
        tick_imb_5 = _get(features, "tick_imbalance_5", default=0.0)
        tick_imb_20 = _get(features, "tick_imbalance_20", default=0.0)

        if abs(tick_imb_5) > 0.05 or abs(tick_imb_20) > 0.05:
            # Weighted average: recent (5) has more weight
            tick_signal = tick_imb_5 * 0.65 + tick_imb_20 * 0.35
            if abs(tick_signal) > 0.60:
                # Strong order flow — high weight
                scores["tick_flow"] = max(-1.0, min(1.0, tick_signal))
            elif abs(tick_signal) > 0.30:
                scores["tick_flow"] = max(-0.5, min(0.5, tick_signal))
            triggered.append("tick_flow")

        # ── 7. Stochastic Crossover ─────────────────────────────────────
        stoch_k = _get(features, "stoch_k")
        stoch_d = _get(features, "stoch_d")

        if stoch_k > 0 or stoch_d > 0:
            if stoch_k > 80 and stoch_d > 80:
                # Overbought zone
                scores["stochastic"] = -0.7
            elif stoch_k < 20 and stoch_d < 20:
                # Oversold zone
                scores["stochastic"] = 0.7
            elif stoch_k > stoch_d:
                # Bullish crossover
                scores["stochastic"] = 0.4
            elif stoch_k < stoch_d:
                # Bearish crossover
                scores["stochastic"] = -0.4
            else:
                scores["stochastic"] = 0.0
            triggered.append("stochastic")

        # ── Weighted aggregation ────────────────────────────────────────

        if not scores:
            logger.warning("rule_based.no_indicators — features may be empty or non-numeric")
            return EnsembleSignal(
                direction="NO_TRADE",
                confidence=0.0,
                individual_scores={},
                regime=_get_str(features, "vol_regime", "MEDIUM"),
            )

        weighted_sum = 0.0
        total_weight = 0.0
        individual_scores: dict[str, float] = {}

        for name, score in scores.items():
            w = self.weights.get(name, 0.0)
            weighted_sum += w * score
            total_weight += w
            individual_scores[f"rule_{name}"] = round(score, 4)

        # Normalize: weighted_sum ∈ [-1, 1]
        if total_weight > 0:
            normalized = weighted_sum / total_weight
        else:
            normalized = 0.0

        confidence = abs(normalized)

        # Boost confidence when trend context aligns
        trend_dir = _get_str(features, "trend_direction")
        trend_strength = _get(features, "trend_strength")
        # trend_strength is on a 0-100 scale from nexus-features — normalize before boost
        trend_boost = min(trend_strength / 100.0, 1.0) * 0.15
        if trend_dir == "UPTREND" and normalized > 0:
            confidence = min(1.0, confidence + trend_boost)
        elif trend_dir == "DOWNTREND" and normalized < 0:
            confidence = min(1.0, confidence + trend_boost)

        # Determine direction
        if confidence < self.confidence_threshold:
            direction = "NO_TRADE"
        elif normalized > 0:
            direction = "BUY"
        else:
            direction = "SELL"

        # Volatility regime
        vol_regime = _get_str(features, "vol_regime", "MEDIUM")
        atr_norm = _get(features, "atr_normalized")

        # Volatility dampening — avoid max-confidence entries in chaos
        if vol_regime == "EXTREME" or atr_norm > 2.5:
            confidence *= 0.70
        elif vol_regime == "HIGH" or atr_norm > 2.0:
            confidence *= 0.85

        confidence = min(confidence, 0.92)

        signal = EnsembleSignal(
            direction=direction,
            confidence=round(confidence, 4),
            individual_scores=individual_scores,
            regime=vol_regime or "MEDIUM",
            regime_confidence=0.0,
            anomaly_score=0.0,
            is_anomaly=False,
            sentiment={},
            top_features=triggered[:10],
        )

        logger.info(
            "rule_based.signal: %s conf=%.3f factors=%s scores=%s",
            direction, confidence, triggered,
            {k: round(v, 3) for k, v in scores.items()},
        )

        return signal
