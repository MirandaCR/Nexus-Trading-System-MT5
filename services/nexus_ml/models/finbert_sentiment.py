"""
FinBERT-based news sentiment scorer.

Wraps the ProsusAI/finbert transformer for financial headline sentiment
analysis with result caching to avoid redundant GPU/CPU inference.
"""
from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

_FINBERT_MODEL = "ProsusAI/finbert"
_MAX_CACHE_SIZE = 4096


class FinBERTSentiment:
    """Lazy-loaded FinBERT pipeline with LRU caching."""

    def __init__(
        self,
        model_name: str = _FINBERT_MODEL,
        device: int = -1,
        max_cache: int = _MAX_CACHE_SIZE,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._pipeline: Any = None
        self._cache: OrderedDict[str, dict[str, float]] = OrderedDict()
        self._max_cache = max_cache

    # ── Lazy init ─────────────────────────────────────────────────────────

    def _ensure_pipeline(self) -> None:
        if self._pipeline is not None:
            return
        try:
            from transformers import pipeline
            self._pipeline = pipeline(
                "sentiment-analysis",
                model=self._model_name,
                tokenizer=self._model_name,
                device=self._device,
                top_k=None,
                truncation=True,
                max_length=512,
            )
            logger.info("FinBERT pipeline loaded (%s, device=%s)", self._model_name, self._device)
        except Exception:
            logger.error("Failed to load FinBERT pipeline", exc_info=True)
            raise

    # ── Single text scoring ───────────────────────────────────────────────

    def score_text(self, text: str) -> dict[str, float]:
        """Score a single headline / text fragment.

        Returns {"positive": p, "negative": n, "neutral": u, "compound": c}
        where compound = positive − negative ∈ [-1, 1].
        """
        key = self._cache_key(text)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        self._ensure_pipeline()
        raw = self._pipeline(text)[0]  # list of {label, score}
        result = self._parse_raw(raw)
        self._put_cache(key, result)
        return result

    # ── Batch scoring ─────────────────────────────────────────────────────

    def score_batch(self, texts: list[str]) -> list[dict[str, float]]:
        results: list[dict[str, float]] = []
        uncached_texts: list[str] = []
        uncached_indices: list[int] = []

        for i, text in enumerate(texts):
            key = self._cache_key(text)
            if key in self._cache:
                self._cache.move_to_end(key)
                results.append(self._cache[key])
            else:
                results.append({})
                uncached_texts.append(text)
                uncached_indices.append(i)

        if uncached_texts:
            self._ensure_pipeline()
            batch_raw = self._pipeline(uncached_texts)
            for idx, raw in zip(uncached_indices, batch_raw):
                parsed = self._parse_raw(raw)
                results[idx] = parsed
                self._put_cache(self._cache_key(texts[idx]), parsed)

        return results

    # ── Internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_raw(raw: list[dict[str, Any]]) -> dict[str, float]:
        scores = {"positive": 0.0, "negative": 0.0, "neutral": 0.0}
        for item in raw:
            label = item["label"].lower()
            if label in scores:
                scores[label] = float(item["score"])
        scores["compound"] = scores["positive"] - scores["negative"]
        return scores

    @staticmethod
    def _cache_key(text: str) -> str:
        return hashlib.md5(text.strip().lower().encode()).hexdigest()

    def _put_cache(self, key: str, value: dict[str, float]) -> None:
        self._cache[key] = value
        if len(self._cache) > self._max_cache:
            self._cache.popitem(last=False)

    def clear_cache(self) -> None:
        self._cache.clear()

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    @property
    def is_loaded(self) -> bool:
        return self._pipeline is not None
