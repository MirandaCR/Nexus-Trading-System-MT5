"""
Nexus News — Social-media sentiment aggregation (lightweight).

Uses keyword scoring with optional FinBERT transformer scoring
to produce a normalised sentiment value in [-1, +1].
Reddit integration removed to reduce dependencies.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# ── Sentiment result ─────────────────────────────────────────────────────────

@dataclass
class SentimentResult:
    score: float = 0.0          # -1 (bearish) → +1 (bullish)
    volume: int = 0             # number of posts / mentions analysed
    positive: int = 0
    negative: int = 0
    neutral: int = 0
    source: str = ""
    sampled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "volume": self.volume,
            "positive": self.positive,
            "negative": self.negative,
            "neutral": self.neutral,
            "source": self.source,
            "sampled_at": self.sampled_at.isoformat(),
        }


# ── Keyword sentiment (fast fallback) ───────────────────────────────────────

_BULLISH: set[str] = {
    "bullish", "moon", "buy", "long", "calls", "rocket",
    "pump", "upside", "breakout", "rally", "green", "higher",
    "strong", "surge", "soar", "gain", "profit", "beat",
}

_BEARISH: set[str] = {
    "bearish", "sell", "short", "puts", "dump", "crash",
    "downside", "breakdown", "plunge", "red", "lower",
    "weak", "drop", "loss", "miss", "recession", "fear",
}


def _keyword_score(text: str) -> float:
    """Quick keyword-based sentiment: returns -1…+1."""
    words = set(text.lower().split())
    bull = len(words & _BULLISH)
    bear = len(words & _BEARISH)
    total = bull + bear
    if total == 0:
        return 0.0
    return (bull - bear) / total


# ── FinBERT scoring (optional, lazy-loaded) ──────────────────────────────────

_finbert_pipeline: Any = None
_finbert_lock = asyncio.Lock()


async def _get_finbert() -> Any | None:
    """Lazy-load the FinBERT pipeline. Returns None if unavailable."""
    global _finbert_pipeline
    if _finbert_pipeline is not None:
        return _finbert_pipeline

    async with _finbert_lock:
        if _finbert_pipeline is not None:
            return _finbert_pipeline

        try:
            from transformers import pipeline as hf_pipeline
            _finbert_pipeline = await asyncio.to_thread(
                hf_pipeline,
                "sentiment-analysis",
                model="ProsusAI/finbert",
                tokenizer="ProsusAI/finbert",
                device=-1,
            )
            log.info("finbert_loaded")
            return _finbert_pipeline
        except Exception as exc:
            log.warning("finbert_unavailable", error=str(exc))
            return None


async def _finbert_score(text: str) -> float:
    """Run FinBERT on *text*, returning -1…+1. Falls back to 0 on error."""
    pipe = await _get_finbert()
    if pipe is None:
        return 0.0

    try:
        truncated = text[:512]
        results = await asyncio.to_thread(pipe, truncated)
        if not results:
            return 0.0
        label = results[0]["label"].lower()
        score = results[0]["score"]
        if label == "positive":
            return score
        elif label == "negative":
            return -score
        return 0.0
    except Exception as exc:
        log.warning("finbert_score_error", error=str(exc))
        return 0.0


class SocialSentiment:
    """Aggregates sentiment from news headlines (Reddit removed)."""

    def __init__(self, use_finbert: bool = True):
        self._use_finbert = use_finbert

    async def aggregate_social_sentiment(self, symbol: str) -> float:
        """Return an overall social sentiment score in [-1, +1] for *symbol*.

        Without Reddit/Twitter, returns 0.0 (neutral). The news-based
        sentiment from FinBERT in the NewsSentinel agent is the primary
        sentiment source now.
        """
        return 0.0

    async def _score_posts(
        self, posts: list[str], source: str,
    ) -> SentimentResult:
        if not posts:
            return SentimentResult(source=source, volume=0)

        positive = 0
        negative = 0
        neutral = 0
        total_score = 0.0

        for text in posts:
            kw_score = _keyword_score(text)

            fb_score = 0.0
            if self._use_finbert:
                fb_score = await _finbert_score(text)

            combined = (kw_score * 0.4 + fb_score * 0.6) if fb_score != 0.0 else kw_score

            if combined > 0.1:
                positive += 1
            elif combined < -0.1:
                negative += 1
            else:
                neutral += 1

            total_score += combined

        avg_score = total_score / len(posts)
        return SentimentResult(
            score=max(-1.0, min(1.0, avg_score)),
            volume=len(posts),
            positive=positive,
            negative=negative,
            neutral=neutral,
            source=source,
        )


def _symbol_keywords(symbol: str) -> list[str]:
    """Convert a trading symbol to social-media search keywords."""
    sym = symbol.upper().replace("/", "")
    keywords = [sym]

    alias_map: dict[str, list[str]] = {
        "EURUSD": ["EUR", "USD", "euro", "dollar"],
        "GBPUSD": ["GBP", "USD", "pound", "cable"],
        "USDJPY": ["USD", "JPY", "yen", "dollar"],
        "XAUUSD": ["gold", "XAU", "precious metal", "oro"],
        "BTCUSD": ["bitcoin", "BTC", "crypto"],
        "ETHUSD": ["ethereum", "ETH", "crypto"],
        "SPX":    ["S&P", "SPX", "sp500", "market"],
    }

    extra = alias_map.get(sym, [])
    if not extra and len(sym) == 6:
        extra = [sym[:3], sym[3:]]

    keywords.extend(extra)
    return keywords
