"""
Strategy Research subsystem.

Houses the dynamic strategy lab: an LLM-driven Strategy Researcher proposes
candidate strategies, runs them through the historical backtester, scores
them against promotion thresholds and persists results in Redis so the
dashboard's Strategy Lab can render them in real time.
"""
from __future__ import annotations

from .registry import StrategyRegistry, StrategyRecord, PromotionRules
from .evaluator import StrategyEvaluator, EvaluationResult
from .researcher import StrategyResearcher

__all__ = [
    "StrategyRegistry",
    "StrategyRecord",
    "PromotionRules",
    "StrategyEvaluator",
    "EvaluationResult",
    "StrategyResearcher",
]
