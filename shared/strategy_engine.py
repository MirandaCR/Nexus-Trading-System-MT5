"""
Executable strategy rule engine — scores signal alignment with active strategy.

Complements LLM advisory by providing deterministic alignment checks.
"""
from __future__ import annotations

from typing import Any


def score_strategy_alignment(
    active_strategy: dict[str, Any],
    *,
    verdict: str,
    regime: str,
    session: str,
    htf_bias: str,
    ml_direction: str = "",
) -> tuple[float, list[str]]:
    """Return (alignment_score 0..1, reasons)."""
    if not active_strategy:
        return 0.5, ["no_active_strategy"]

    score = 0.5
    reasons: list[str] = []
    stype = (active_strategy.get("type") or "").lower()
    entry = (active_strategy.get("entry_logic") or "").lower()

    # Direction alignment with ML signal
    if ml_direction and verdict == ml_direction:
        score += 0.15
        reasons.append("ml_direction_match")

    # Regime-type fit
    regime_u = regime.upper()
    if "trend" in stype or "ema" in entry:
        if regime_u == "TRENDING":
            score += 0.2
            reasons.append("trend_strategy_in_trending_regime")
        elif regime_u == "RANGING":
            score -= 0.15
            reasons.append("trend_strategy_in_ranging_regime")
    elif "mean" in stype or "reversion" in stype or "bounce" in entry:
        if regime_u == "RANGING":
            score += 0.2
            reasons.append("reversion_strategy_in_ranging_regime")
    elif "breakout" in stype or "breakout" in entry:
        if regime_u == "BREAKOUT":
            score += 0.2
            reasons.append("breakout_strategy_in_breakout_regime")

    # HTF bias alignment
    if htf_bias in ("BULLISH", "BEARISH"):
        if (verdict == "BUY" and htf_bias == "BULLISH") or (verdict == "SELL" and htf_bias == "BEARISH"):
            score += 0.1
            reasons.append("htf_bias_aligned")

    # Session keywords in entry logic
    if session and session.lower() in entry:
        score += 0.05
        reasons.append("session_mentioned_in_strategy")

    return max(0.0, min(1.0, score)), reasons


def should_boost_conviction(alignment_score: float, threshold: float = 0.7) -> int:
    """Bonus conviction points when strategy alignment is strong."""
    if alignment_score >= threshold:
        return 10
    if alignment_score >= 0.6:
        return 5
    if alignment_score < 0.35:
        return -10
    return 0
