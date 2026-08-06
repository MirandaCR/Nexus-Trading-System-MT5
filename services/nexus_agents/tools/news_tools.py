"""
News and sentiment tools used by the News Sentinel (A2) and other agents.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from shared.http_client import new_client
import structlog

logger = structlog.get_logger(__name__)

NEWS_SERVICE_URL = os.getenv("NEXUS_NEWS_URL", "http://localhost:8005")


async def fetch_news(query: str) -> list[dict[str, Any]]:
    """Fetch latest news items matching the query from the news service."""
    try:
        async with new_client(timeout=5.0) as client:
            resp = await client.get(
                f"{NEWS_SERVICE_URL}/api/news/latest",
                params={"q": query, "limit": 20},
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    return data.get("items", [])
                if isinstance(data, list):
                    return data
    except Exception as exc:
        await logger.awarning("tools.fetch_news_failed", error=str(exc))

    return []


async def get_calendar(hours: int = 24) -> list[dict[str, Any]]:
    """Get upcoming economic calendar events within the specified hours."""
    try:
        async with new_client(timeout=5.0) as client:
            resp = await client.get(
                f"{NEWS_SERVICE_URL}/api/news/calendar",
                params={"hours": hours},
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    return data.get("events", data.get("items", []))
                if isinstance(data, list):
                    return data
    except Exception as exc:
        await logger.awarning("tools.get_calendar_failed", error=str(exc))

    return []


async def get_sentiment(symbol: str) -> dict[str, Any]:
    """Get aggregate sentiment score for a symbol."""
    try:
        async with new_client(timeout=5.0) as client:
            resp = await client.get(
                f"{NEWS_SERVICE_URL}/api/news/sentiment/{symbol}"
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as exc:
        await logger.awarning("tools.get_sentiment_failed", error=str(exc))

    return {"symbol": symbol, "score": 0.0, "label": "neutral", "sources": 0}


async def finbert_score(text: str) -> dict[str, Any]:
    """
    Run FinBERT sentiment analysis on a text snippet.

    Attempts to call the news service FinBERT endpoint; falls back to
    a local transformers pipeline if available.
    """
    try:
        async with new_client(timeout=10.0) as client:
            resp = await client.post(
                f"{NEWS_SERVICE_URL}/api/finbert",
                json={"text": text},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass

    try:
        from transformers import pipeline

        _finbert = pipeline(
            "sentiment-analysis",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            device=-1,
        )
        results = _finbert(text[:512])
        if results:
            r = results[0]
            label = r.get("label", "neutral").lower()
            score_val = r.get("score", 0.0)
            if label == "negative":
                score_val = -score_val
            elif label == "neutral":
                score_val = 0.0
            return {"text": text[:100], "label": label, "score": score_val}
    except Exception as exc:
        await logger.awarning("tools.finbert_fallback_failed", error=str(exc))

    return {"text": text[:100], "label": "neutral", "score": 0.0}
