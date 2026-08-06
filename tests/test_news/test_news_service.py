"""Tests for nexus_news service — fetcher, calendar, sentiment."""
from __future__ import annotations

from pathlib import Path

import pytest


NEWS_SVC = Path(__file__).resolve().parents[2] / "services" / "nexus_news"


class TestNewsServiceFiles:
    REQUIRED = [
        "__init__.py", "main.py", "news_fetcher.py",
        "calendar_parser.py", "social_sentiment.py",
    ]

    @pytest.mark.parametrize("filename", REQUIRED)
    def test_file_exists(self, filename: str):
        assert (NEWS_SVC / filename).exists(), f"Missing news file: {filename}"


class TestNewsFetcherImport:
    def test_importable(self):
        import importlib
        mod = importlib.import_module("services.nexus_news.news_fetcher")
        assert mod is not None


class TestCalendarParserImport:
    def test_importable(self):
        import importlib
        mod = importlib.import_module("services.nexus_news.calendar_parser")
        assert mod is not None


class TestNewsModelValidation:
    def test_news_item_creation(self, sample_news_item):
        assert sample_news_item.headline == "Fed holds rates steady"
        assert sample_news_item.impact == "high"

    def test_news_item_default_impact(self):
        from shared.models import NewsItem
        item = NewsItem(headline="Minor data release")
        assert item.impact == "low"
        assert item.sentiment == "neutral"
