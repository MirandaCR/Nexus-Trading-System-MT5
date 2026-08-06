"""
Nexus News — multi-source news aggregation with rate limiting and deduplication.

Integrates NewsAPI and GNews, maps headlines to affected currencies/assets,
and provides a unified async interface consumed by the polling loop in main.py.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from shared.http_client import new_client
import structlog
from pydantic import BaseModel, Field

from shared.models import NewsItem

log = structlog.get_logger(__name__)

# ── Currency / asset keyword mapping ─────────────────────────────────────────

CURRENCY_KEYWORDS: dict[str, list[str]] = {
    "USD": ["dollar", "usd", "fed", "fomc", "treasury", "us economy", "nonfarm", "cpi"],
    "EUR": ["euro", "eur", "ecb", "eurozone", "eu economy"],
    "GBP": ["pound", "gbp", "boe", "bank of england", "uk economy"],
    "JPY": ["yen", "jpy", "boj", "bank of japan", "japan economy"],
    "AUD": ["aussie", "aud", "rba", "australia economy"],
    "NZD": ["kiwi", "nzd", "rbnz", "new zealand economy"],
    "CAD": ["loonie", "cad", "boc", "canada economy", "oil price"],
    "CHF": ["franc", "chf", "snb", "swiss economy"],
    "XAU": ["gold", "xau", "precious metal", "bullion"],
    "BTC": ["bitcoin", "btc", "crypto"],
    "ETH": ["ethereum", "eth"],
    "SPX": ["s&p", "spx", "sp500", "wall street"],
}

# ── Rate limiter ─────────────────────────────────────────────────────────────

class _RateLimiter:
    """Token-bucket rate limiter per API source."""

    def __init__(self, calls_per_minute: int = 30):
        self._interval = 60.0 / calls_per_minute
        self._last_call: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


# ── Fetcher ──────────────────────────────────────────────────────────────────

class NewsFetcher:
    """Aggregates headlines from NewsAPI and GNews with deduplication."""

    def __init__(
        self,
        newsapi_key: str = "",
        gnews_key: str = "",
        cache_ttl_sec: int = 120,
    ):
        self._newsapi_key = newsapi_key
        self._gnews_key = gnews_key
        self._cache_ttl = cache_ttl_sec

        self._newsapi_limiter = _RateLimiter(calls_per_minute=25)
        self._gnews_limiter = _RateLimiter(calls_per_minute=10)

        self._seen_hashes: dict[str, float] = {}
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = new_client(timeout=15.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── NewsAPI ──────────────────────────────────────────────────────────────

    async def fetch_newsapi(
        self, query: str, language: str = "en"
    ) -> list[NewsItem]:
        if not self._newsapi_key:
            log.warning("newsapi_key not configured — skipping NewsAPI")
            return []

        await self._newsapi_limiter.acquire()
        client = await self._get_client()

        params: dict[str, Any] = {
            "q": query,
            "language": language,
            "sortBy": "publishedAt",
            "pageSize": 20,
            "apiKey": self._newsapi_key,
        }

        try:
            resp = await client.get(
                "https://newsapi.org/v2/everything", params=params
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                log.warning("newsapi_rate_limited", query=query)
            else:
                log.error("newsapi_error", status=exc.response.status_code)
            return []
        except httpx.HTTPError as exc:
            log.error("newsapi_http_error", error=str(exc))
            return []

        items: list[NewsItem] = []
        for article in data.get("articles", []):
            headline = (article.get("title") or "").strip()
            if not headline:
                continue

            published = article.get("publishedAt", "")
            ts = _parse_iso(published) if published else datetime.now(timezone.utc)

            items.append(
                NewsItem(
                    headline=headline,
                    source=article.get("source", {}).get("name", "NewsAPI"),
                    timestamp=ts,
                    affected_currencies=_map_currencies(headline),
                )
            )

        log.debug("newsapi_fetched", query=query, count=len(items))
        return items

    # ── GNews ────────────────────────────────────────────────────────────────

    async def fetch_gnews(self, query: str) -> list[NewsItem]:
        if not self._gnews_key:
            log.warning("gnews_key not configured — skipping GNews")
            return []

        await self._gnews_limiter.acquire()
        client = await self._get_client()

        params: dict[str, Any] = {
            "q": query,
            "lang": "en",
            "max": 10,
            "token": self._gnews_key,
        }

        try:
            resp = await client.get(
                "https://gnews.io/api/v4/search", params=params
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                log.warning("gnews_rate_limited", query=query)
            else:
                log.error("gnews_error", status=exc.response.status_code)
            return []
        except httpx.HTTPError as exc:
            log.error("gnews_http_error", error=str(exc))
            return []

        items: list[NewsItem] = []
        for article in data.get("articles", []):
            headline = (article.get("title") or "").strip()
            if not headline:
                continue

            published = article.get("publishedAt", "")
            ts = _parse_iso(published) if published else datetime.now(timezone.utc)

            items.append(
                NewsItem(
                    headline=headline,
                    source=article.get("source", {}).get("name", "GNews"),
                    timestamp=ts,
                    affected_currencies=_map_currencies(headline),
                )
            )

        log.debug("gnews_fetched", query=query, count=len(items))
        return items

    # ── Aggregator ───────────────────────────────────────────────────────────

    async def fetch_all(self, symbols: list[str]) -> list[NewsItem]:
        """Fetch from all sources for the given symbols, deduplicate, and return."""
        queries = _symbols_to_queries(symbols)
        tasks: list[asyncio.Task[list[NewsItem]]] = []

        for q in queries:
            tasks.append(asyncio.create_task(self.fetch_newsapi(q)))
            tasks.append(asyncio.create_task(self.fetch_gnews(q)))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        combined: list[NewsItem] = []
        for res in results:
            if isinstance(res, BaseException):
                log.error("fetch_all_task_error", error=str(res))
                continue
            combined.extend(res)

        deduped = self._deduplicate(combined)
        log.info("fetch_all_complete", raw=len(combined), deduped=len(deduped))
        return deduped

    # ── Deduplication ────────────────────────────────────────────────────────

    def _deduplicate(self, items: list[NewsItem]) -> list[NewsItem]:
        self._evict_stale()
        unique: list[NewsItem] = []
        now = time.monotonic()

        for item in items:
            h = _headline_hash(item.headline)
            if h not in self._seen_hashes:
                self._seen_hashes[h] = now
                unique.append(item)

        return unique

    def _evict_stale(self) -> None:
        now = time.monotonic()
        stale = [k for k, t in self._seen_hashes.items() if now - t > self._cache_ttl]
        for k in stale:
            del self._seen_hashes[k]


# ── Module-level helpers ─────────────────────────────────────────────────────

def _headline_hash(headline: str) -> str:
    normalised = headline.lower().strip()
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]


def _map_currencies(text: str) -> list[str]:
    text_lower = text.lower()
    return [
        ccy
        for ccy, keywords in CURRENCY_KEYWORDS.items()
        if any(kw in text_lower for kw in keywords)
    ]


def _symbols_to_queries(symbols: list[str]) -> list[str]:
    """Convert trading symbols like EURUSD into search-friendly queries."""
    queries: set[str] = set()
    for sym in symbols:
        sym_upper = sym.upper().replace("/", "")
        if len(sym_upper) == 6:
            queries.add(sym_upper[:3])
            queries.add(sym_upper[3:])
        else:
            queries.add(sym_upper)

    keyword_map: dict[str, str] = {
        "USD": "US dollar economy",
        "EUR": "Euro eurozone economy",
        "GBP": "British pound economy",
        "JPY": "Japanese yen economy",
        "AUD": "Australian dollar economy",
        "NZD": "New Zealand dollar economy",
        "CAD": "Canadian dollar economy",
        "CHF": "Swiss franc economy",
        "XAU": "gold price",
        "BTC": "bitcoin crypto",
        "ETH": "ethereum crypto",
    }
    return [keyword_map.get(q, q) for q in queries]


def _parse_iso(raw: str) -> datetime:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)
