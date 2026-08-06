"""
A0 — El Vigía: Regime Analyst agent.

Classifies market as TRENDING / RANGING / BREAKOUT using advanced
technical analysis from the features pipeline (trend_analyzer, volatility,
key_levels).  When the market is ranging, it identifies range boundaries
and suggests mean-reversion setups.  This information enriches every
downstream agent so they can adapt their strategies.

An optional Gaussian HMM layer provides a probabilistic second opinion on
regime classification, capturing latent state transitions that rule-based
scoring can miss.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from shared.redis_client import RedisClient

from ..state import AgentState
from .base_agent import BaseAgent

logger = structlog.get_logger(__name__)

# ── HMM integration (graceful if hmmlearn is missing or model untrained) ──
_hmm_detector = None
_HMM_CONFIDENCE_OVERRIDE = float(os.getenv("NEXUS_HMM_OVERRIDE_CONF", "0.7"))

try:
    from services.nexus_ml.models.hmm_regime import HMMRegimeDetector

    _HMM_MODEL_PATH = Path(
        os.getenv("MODEL_DIR", "./models_store")
    ) / "hmm_regime.pkl"

    if HMMRegimeDetector.is_available():
        _hmm_detector = HMMRegimeDetector()
        if _HMM_MODEL_PATH.exists():
            try:
                _hmm_detector.load(_HMM_MODEL_PATH)
                logger.info("hmm.loaded", path=str(_HMM_MODEL_PATH))
            except Exception as _exc:
                logger.warning("hmm.load_failed", error=str(_exc))
                _hmm_detector = None
        else:
            logger.info("hmm.no_saved_model", path=str(_HMM_MODEL_PATH))
            _hmm_detector = None
except ImportError:
    logger.info("hmm.unavailable", reason="nexus_ml.models.hmm_regime not importable")

# ADX thresholds for regime detection
_ADX_TRENDING = float(os.getenv("NEXUS_ADX_TRENDING", "25"))
_ADX_STRONG_TREND = float(os.getenv("NEXUS_ADX_STRONG_TREND", "40"))

# BB bandwidth thresholds — note: the features pipeline produces
# ``bb_bandwidth`` = (upper − lower) / mid  (a RATIO, not percentage).
# For gold (close ~$2650), typical values are 0.01-0.05.
# Squeeze means bands are contracting → low bandwidth.
_BB_SQUEEZE_THRESHOLD = float(os.getenv("NEXUS_BB_SQUEEZE_THRESHOLD", "0.015"))
_BB_WIDE_THRESHOLD = float(os.getenv("NEXUS_BB_WIDE_THRESHOLD", "0.04"))

# Map feature-pipeline trend_direction values to A0's internal names
_TREND_MAP: dict[str, str] = {
    "UPTREND": "BULLISH",
    "DOWNTREND": "BEARISH",
    "SIDEWAYS": "NEUTRAL",
    "BULLISH": "BULLISH",
    "BEARISH": "BEARISH",
    "NEUTRAL": "NEUTRAL",
}


class RegimeAnalystAgent(BaseAgent):
    """Detects the current market regime before all other analysis agents."""

    def __init__(self, *, redis_client: RedisClient | None = None) -> None:
        super().__init__("A0", "Regime Analyst", redis_client=redis_client)

    async def invoke(self, state: AgentState) -> AgentState:
        symbol = state["symbol"]
        features = state.get("features", {})
        htf_ctx = state.get("htf_context", {})

        regime = self._classify_regime(features)
        range_info = self._detect_range_boundaries(features) if regime["regime"] == "RANGING" else {}
        breakout_signals = self._detect_breakout_signals(features) if regime["regime"] != "TRENDING" else {}

        # Fetch advanced S/R and order blocks from features cache
        sr_levels = await self._get_advanced_levels(symbol)

        # ── HMM probabilistic regime detection ─────────────────────────────
        hmm_result = self._run_hmm(features)
        effective_regime = regime["regime"]

        if hmm_result.get("hmm_regime") and hmm_result["hmm_regime"] != "UNKNOWN":
            hmm_mapped = self._map_hmm_to_rule_regime(hmm_result["hmm_regime"])
            if (
                hmm_mapped != effective_regime
                and hmm_result["hmm_confidence"] > _HMM_CONFIDENCE_OVERRIDE
            ):
                logger.info(
                    "hmm.override",
                    rule_regime=effective_regime,
                    hmm_regime=hmm_result["hmm_regime"],
                    hmm_confidence=hmm_result["hmm_confidence"],
                )
                effective_regime = hmm_mapped

        regime_analysis: dict[str, Any] = {
            "regime": effective_regime,
            "regime_strength": regime["strength"],
            "regime_score": regime["score"],
            "adx": regime["adx"],
            "bb_bandwidth": regime["bb_bandwidth"],
            "vol_regime": regime["vol_regime"],
            "trend_direction": regime.get("trend_direction", "NEUTRAL"),
            "range_info": range_info,
            "breakout_signals": breakout_signals,
            "sr_levels": sr_levels,
            "suggested_approach": self._suggest_approach(regime, range_info, breakout_signals),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "rule_based_regime": regime["regime"],
            **hmm_result,
        }

        state["regime_analysis"] = regime_analysis

        hmm_tag = ""
        if hmm_result.get("hmm_regime"):
            hmm_tag = (
                f" | HMM: {hmm_result['hmm_regime']}"
                f"({hmm_result['hmm_confidence']:.0%})"
            )

        summary = (
            f"REGIME: {effective_regime} (strength={regime['strength']}/100, "
            f"ADX={regime['adx']:.1f}, BB_bw={regime['bb_bandwidth']:.4f}) | "
            f"Trend: {regime['trend_direction']} | "
            f"Vol: {regime['vol_regime']} | "
            f"Approach: {regime_analysis['suggested_approach']}"
            f"{hmm_tag}"
        )

        if range_info:
            summary += (
                f" | Range: {range_info.get('range_low', 0):.5f}-"
                f"{range_info.get('range_high', 0):.5f}"
            )

        state["messages"].append({
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "type": "regime",
            "content": summary,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        await self._publish_message(summary, metadata=regime_analysis, state=state)
        await self._log_reasoning(summary, state=state)

        return state

    # ── Core Classification ──────────────────────────────────────────────

    def _classify_regime(self, features: dict) -> dict[str, Any]:
        """Classify market as TRENDING / RANGING / BREAKOUT.

        Uses a composite score built from ADX, Bollinger bandwidth, trend
        score (EMA alignment + market structure + Ichimoku), DI spread,
        RSI zones, and the vol-regime label already computed by the
        features pipeline.

        Key notes on metric alignment:
          - ``bb_bandwidth`` is the raw ratio ``(upper-lower)/mid``.
            For gold (~$2650) this is typically 0.01-0.05.
          - ``atr_normalized`` is ATR/close × 100 (a percentage).
            Gold ATR $8 → 0.30%.  Spike → 0.8%+.
          - ``trend_direction`` from the features pipeline uses
            UPTREND/DOWNTREND/SIDEWAYS; we map to BULLISH/BEARISH/NEUTRAL
            via ``_TREND_MAP`` for consistency with downstream agents.
        """
        adx = float(features.get("adx", 0))
        di_plus = float(features.get("di_plus", features.get("plus_di", 0)))
        di_minus = float(features.get("di_minus", features.get("minus_di", 0)))

        # Prefer bb_bandwidth (the field the features pipeline actually
        # produces); fall back to bb_width only if present.
        bb_bw = float(
            features.get("bb_bandwidth", features.get("bb_width", 0))
        )
        atr_norm = float(features.get("atr_normalized", 0))
        rsi = float(features.get("rsi", 50))
        trend_score = float(features.get("trend_score", 0))
        raw_trend_dir = str(features.get("trend_direction", "NEUTRAL"))
        trend_dir = _TREND_MAP.get(raw_trend_dir.upper(), "NEUTRAL")
        vol_regime = str(features.get("vol_regime", "MEDIUM")).upper()
        macd_hist = float(
            features.get("macd_histogram", features.get("macd_hist", 0))
        )

        # ── Composite regime score: positive = trending, negative = ranging ──
        score = 0.0

        # ADX contribution (biggest weight)
        if adx >= _ADX_STRONG_TREND:
            score += 40
        elif adx >= _ADX_TRENDING:
            score += 20
        elif adx < 20:
            score -= 25

        # BB bandwidth contribution (corrected thresholds for ratio metric)
        if bb_bw > 0:
            if bb_bw < _BB_SQUEEZE_THRESHOLD:
                score -= 20  # squeeze = compression = ranging or pre-breakout
            elif bb_bw > _BB_WIDE_THRESHOLD:
                score += 15  # wide bands = trending

        # Trend score from features pipeline (already integrates EMA
        # alignment, ADX, market structure, Ichimoku — range -100 to +100)
        if abs(trend_score) > 50:
            score += 20
        elif abs(trend_score) < 20:
            score -= 15

        # DI spread — big spread = trending
        di_spread = abs(di_plus - di_minus)
        if di_spread > 15:
            score += 10
        elif di_spread < 5:
            score -= 10

        # RSI extremes suggest directional momentum
        if rsi > 70 or rsi < 30:
            score += 10
        elif 45 < rsi < 55:
            score -= 10  # neutral zone = ranging

        # Vol regime from features pipeline (ATR percentile based)
        if vol_regime == "EXTREME":
            score += 15
        elif vol_regime == "HIGH":
            score += 5
        elif vol_regime == "LOW":
            score -= 15

        # ── Breakout detection ──────────────────────────────────────
        # Requires BOTH: (a) bands were recently squeezed AND (b) ATR
        # is now expanding well above normal.  The macd_hist check was
        # too loose (any non-zero value); replaced with a meaningful
        # momentum threshold.
        is_breakout = (
            bb_bw < _BB_SQUEEZE_THRESHOLD
            and atr_norm > 0.6          # ATR > 0.6% of price (significant move)
            and abs(macd_hist) > 0.5     # real momentum, not noise
            and vol_regime in ("HIGH", "EXTREME")
        )

        if is_breakout:
            regime = "BREAKOUT"
            strength = min(100, max(40, int(50 + atr_norm * 20)))
        elif score >= 15:
            regime = "TRENDING"
            strength = min(100, max(20, int(score + 50)))
        else:
            regime = "RANGING"
            strength = min(100, max(20, int(50 - score)))

        return {
            "regime": regime,
            "strength": strength,
            "score": round(score, 1),
            "adx": adx,
            "bb_bandwidth": bb_bw,
            "vol_regime": vol_regime,
            "trend_direction": trend_dir,
            "di_plus": di_plus,
            "di_minus": di_minus,
        }

    # ── HMM helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _run_hmm(features: dict) -> dict[str, Any]:
        """Run the HMM detector and return enrichment keys (empty on failure)."""
        if _hmm_detector is None or not _hmm_detector.is_fitted:
            return {}
        try:
            info = _hmm_detector.predict_regime_info(features)
            transmat = _hmm_detector.get_labelled_transition_matrix()
            return {
                "hmm_regime": info.regime,
                "hmm_confidence": info.confidence,
                "hmm_state_probs": info.state_probabilities,
                "hmm_suggested_bias": info.suggested_bias,
                "hmm_volatility_state": info.volatility_state,
                "hmm_transition_matrix": transmat,
            }
        except Exception as exc:
            logger.warning("hmm.predict_failed", error=str(exc))
            return {}

    @staticmethod
    def _map_hmm_to_rule_regime(hmm_regime: str) -> str:
        """Map the 4-state HMM labels back to the rule-based regime names."""
        return {
            "TRENDING_UP": "TRENDING",
            "TRENDING_DOWN": "TRENDING",
            "RANGING": "RANGING",
            "HIGH_VOLATILITY": "BREAKOUT",
        }.get(hmm_regime, "RANGING")

    def _detect_range_boundaries(self, features: dict) -> dict[str, Any]:
        """Identify range boundaries from Bollinger Bands and recent S/R."""
        bb_upper = float(features.get("bb_upper", 0))
        bb_lower = float(features.get("bb_lower", 0))
        bb_middle = float(features.get("bb_middle", features.get("bb_mid", 0)))
        close = float(features.get("close", 0))

        if bb_upper <= 0 or bb_lower <= 0 or close <= 0:
            return {}

        range_width = bb_upper - bb_lower
        position_in_range = (close - bb_lower) / range_width if range_width > 0 else 0.5

        # Determine proximity to boundaries
        near_support = position_in_range < 0.25
        near_resistance = position_in_range > 0.75
        at_midpoint = 0.4 < position_in_range < 0.6

        return {
            "range_high": round(bb_upper, 6),
            "range_low": round(bb_lower, 6),
            "range_mid": round(bb_middle, 6),
            "range_width": round(range_width, 6),
            "position_in_range": round(position_in_range, 3),
            "near_support": near_support,
            "near_resistance": near_resistance,
            "at_midpoint": at_midpoint,
            "mean_reversion_setup": (
                "BUY" if near_support else "SELL" if near_resistance else "WAIT"
            ),
        }

    def _detect_breakout_signals(self, features: dict) -> dict[str, Any]:
        """Detect potential breakout conditions.

        Uses ``bb_bandwidth`` (ratio, not percentage) with the corrected
        squeeze threshold, and requires meaningful ATR expansion.
        """
        bb_bw = float(
            features.get("bb_bandwidth", features.get("bb_width", 0))
        )
        atr_norm = float(features.get("atr_normalized", 0))
        close = float(features.get("close", 0))
        bb_upper = float(features.get("bb_upper", 0))
        bb_lower = float(features.get("bb_lower", 0))
        volume = float(features.get("volume", 0))
        vol_sma = float(
            features.get("volume_sma", features.get("volume_sma_20", volume or 1))
        )

        volume_surge = volume / vol_sma if vol_sma > 0 else 1.0

        return {
            "squeeze_active": bb_bw < _BB_SQUEEZE_THRESHOLD,
            "atr_expanding": atr_norm > 0.6,
            "volume_surge": round(volume_surge, 2),
            "breakout_direction": (
                "UP" if close > bb_upper else "DOWN" if close < bb_lower else "NONE"
            ),
            "breakout_probability": min(1.0, round(
                (0.3 if bb_bw < _BB_SQUEEZE_THRESHOLD else 0)
                + (0.3 if atr_norm > 0.6 else 0)
                + (0.2 if volume_surge > 1.5 else 0)
                + (0.2 if close > bb_upper or close < bb_lower else 0),
                2,
            )),
        }

    def _suggest_approach(
        self,
        regime: dict,
        range_info: dict,
        breakout_signals: dict,
    ) -> str:
        """Suggest trading approach based on regime.

        ``trend_direction`` is already normalised to BULLISH / BEARISH /
        NEUTRAL by ``_TREND_MAP`` inside ``_classify_regime``.
        """
        r = regime["regime"]
        trend = regime.get("trend_direction", "NEUTRAL").upper()

        if r == "TRENDING":
            if trend == "BULLISH":
                return "TREND_FOLLOW_LONG"
            elif trend == "BEARISH":
                return "TREND_FOLLOW_SHORT"
            return "TREND_FOLLOW"
        elif r == "BREAKOUT":
            bd = breakout_signals.get("breakout_direction", "NONE")
            if bd == "UP":
                return "BREAKOUT_LONG"
            elif bd == "DOWN":
                return "BREAKOUT_SHORT"
            return "BREAKOUT_WATCH"
        else:  # RANGING
            mrs = range_info.get("mean_reversion_setup", "WAIT")
            if mrs == "BUY":
                return "MEAN_REVERSION_LONG"
            elif mrs == "SELL":
                return "MEAN_REVERSION_SHORT"
            return "RANGE_WAIT"

    async def _get_advanced_levels(self, symbol: str) -> dict[str, Any]:
        """Fetch advanced S/R levels from features cache."""
        if not self.redis:
            return {}
        try:
            for tf in ("M15", "H1", "M5"):
                raw = await self.redis.cache_get(f"nexus:features:{symbol}:{tf}")
                if raw and isinstance(raw, dict):
                    feats = raw.get("features", raw)
                    return {
                        "sr_count": len(feats.get("sr_levels", [])),
                        "order_blocks": feats.get("order_blocks", []),
                    }
        except Exception:
            pass
        return {}
