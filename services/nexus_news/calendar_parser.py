"""
Nexus News — Economic calendar parser.

Scrapes free ForexFactory-style calendar data, classifies impact,
maps events to affected currencies, and exposes helpers for the
"news blackout" window used by the risk service.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from enum import StrEnum, unique
from typing import Any

import httpx
from shared.http_client import new_client
import structlog
from bs4 import BeautifulSoup

from shared.models import CalendarEvent

log = structlog.get_logger(__name__)

# ── Impact classification ────────────────────────────────────────────────────

@unique
class Impact(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


IMPACT_KEYWORDS_HIGH: set[str] = {
    "interest rate", "rate decision", "nonfarm", "non-farm", "nfp",
    "cpi", "gdp", "fomc", "ecb press", "boe rate", "boj rate",
    "unemployment rate", "retail sales", "pmi", "inflation",
    "monetary policy", "trade balance", "employment change",
}

IMPACT_KEYWORDS_MEDIUM: set[str] = {
    "housing", "consumer confidence", "durable goods", "industrial production",
    "ppi", "building permits", "existing home", "new home",
    "manufacturing", "services", "ism",
}


def _classify_impact(event_name: str, raw_impact: str = "") -> str:
    raw_lower = raw_impact.lower().strip()
    if raw_lower in ("high", "medium", "low"):
        return raw_lower

    name_lower = event_name.lower()
    if any(kw in name_lower for kw in IMPACT_KEYWORDS_HIGH):
        return Impact.HIGH
    if any(kw in name_lower for kw in IMPACT_KEYWORDS_MEDIUM):
        return Impact.MEDIUM
    return Impact.LOW


# ── Calendar parser ──────────────────────────────────────────────────────────

_FOREX_FACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_INVESTING_AJAX_URL = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"
_INVESTING_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
}


class CalendarParser:
    """Fetches and caches economic calendar data."""

    def __init__(self, cache_ttl_sec: int = 300):
        self._cache_ttl = cache_ttl_sec
        self._events: list[CalendarEvent] = []
        self._last_fetch: float = 0.0
        self._lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = new_client(timeout=20.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Primary fetch ────────────────────────────────────────────────────────

    async def fetch_calendar(
        self, date_range: tuple[datetime, datetime] | None = None,
    ) -> list[CalendarEvent]:
        """Fetch this week's calendar, merging ForexFactory + investing.com."""
        async with self._lock:
            now = asyncio.get_event_loop().time()
            if self._events and (now - self._last_fetch) < self._cache_ttl:
                return self._filter_range(self._events, date_range)

        ff_events, inv_events = await asyncio.gather(
            self._fetch_ff_json(), self._fetch_investing_com(),
            return_exceptions=True,
        )
        if isinstance(ff_events, BaseException):
            log.error("calendar_ff_fetch_failed", error=str(ff_events))
            ff_events = []
        if isinstance(inv_events, BaseException):
            log.error("calendar_investing_fetch_failed", error=str(inv_events))
            inv_events = []

        if not ff_events:
            ff_events = await self._fetch_ff_html_fallback()

        events = _merge_events(ff_events, inv_events)

        async with self._lock:
            self._events = events
            self._last_fetch = asyncio.get_event_loop().time()

        return self._filter_range(events, date_range)

    # ── investing.com source (complementary) ──────────────────────────────────

    async def _fetch_investing_com(self) -> list[CalendarEvent]:
        """Fetch this week's calendar from investing.com's AJAX filter endpoint.

        Complements ForexFactory — different providers occasionally cover
        different regions/events, so results are merged rather than used
        only as a fallback.
        """
        client = await self._get_client()
        now = datetime.now(timezone.utc)
        date_from = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        date_to = (now + timedelta(days=6)).strftime("%Y-%m-%d")

        payload = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "timeZone": "0",
            "timeFilter": "timeRemain",
            "currentTab": "custom",
            "limit_from": "0",
        }

        try:
            resp = await client.post(
                _INVESTING_AJAX_URL, headers=_INVESTING_HEADERS, data=payload,
            )
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.error("calendar_investing_fetch_error", error=str(exc))
            return []

        html_fragment = body.get("data", "")
        if not html_fragment:
            return []

        events = _parse_investing_html(html_fragment)
        log.info("calendar_investing_fetched", count=len(events))
        return events

    # ── JSON source (faireconomy mirror) ─────────────────────────────────────

    async def _fetch_ff_json(self) -> list[CalendarEvent]:
        client = await self._get_client()
        try:
            resp = await client.get(_FOREX_FACTORY_URL)
            resp.raise_for_status()
            raw_events: list[dict[str, Any]] = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.error("calendar_json_fetch_error", error=str(exc))
            return []

        events: list[CalendarEvent] = []
        for raw in raw_events:
            event_name = raw.get("title", "").strip()
            currency = raw.get("country", "").upper().strip()
            raw_impact = raw.get("impact", "").strip()
            date_str = raw.get("date", "")

            ts = _parse_calendar_ts(date_str)
            impact = _classify_impact(event_name, raw_impact)

            events.append(
                CalendarEvent(
                    event_name=event_name,
                    currency=currency,
                    impact=impact,
                    forecast=raw.get("forecast") or None,
                    previous=raw.get("previous") or None,
                    actual=raw.get("actual") or None,
                    timestamp=ts,
                )
            )

        log.info("calendar_json_fetched", count=len(events))
        return events

    # ── HTML fallback (scrape ForexFactory) ──────────────────────────────────

    async def _fetch_ff_html_fallback(self) -> list[CalendarEvent]:
        client = await self._get_client()
        url = "https://www.forexfactory.com/calendar"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        try:
            resp = await client.get(url, headers=headers, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("calendar_html_fetch_error", error=str(exc))
            return []

        return _parse_ff_html(resp.text)

    # ── Query helpers ────────────────────────────────────────────────────────

    async def get_upcoming_events(self, minutes: int = 60) -> list[CalendarEvent]:
        events = await self.fetch_calendar()
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(minutes=minutes)
        return [e for e in events if now <= e.timestamp <= cutoff]

    async def get_high_impact_events(self, minutes: int = 30) -> list[CalendarEvent]:
        upcoming = await self.get_upcoming_events(minutes=minutes)
        return [e for e in upcoming if e.impact == Impact.HIGH]

    async def is_news_blackout(self, minutes: int = 5) -> bool:
        """True if a HIGH-impact event is within *minutes* from now."""
        high = await self.get_high_impact_events(minutes=minutes)
        return len(high) > 0

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _filter_range(
        events: list[CalendarEvent],
        rng: tuple[datetime, datetime] | None,
    ) -> list[CalendarEvent]:
        if rng is None:
            return events
        start, end = rng
        return [e for e in events if start <= e.timestamp <= end]


# ── HTML parser (ForexFactory) ───────────────────────────────────────────────

def _parse_ff_html(html: str) -> list[CalendarEvent]:
    """Best-effort parse of ForexFactory calendar HTML."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        log.error("calendar_html_parse_failed")
        return []

    events: list[CalendarEvent] = []
    rows = soup.select("tr.calendar__row")

    current_date: str = ""
    for row in rows:
        date_cell = row.select_one("td.calendar__date")
        if date_cell:
            text = date_cell.get_text(strip=True)
            if text:
                current_date = text

        currency_cell = row.select_one("td.calendar__currency")
        event_cell = row.select_one("td.calendar__event")
        impact_cell = row.select_one("td.calendar__impact")
        forecast_cell = row.select_one("td.calendar__forecast")
        previous_cell = row.select_one("td.calendar__previous")
        actual_cell = row.select_one("td.calendar__actual")
        time_cell = row.select_one("td.calendar__time")

        if not event_cell:
            continue

        event_name = event_cell.get_text(strip=True)
        currency = currency_cell.get_text(strip=True).upper() if currency_cell else ""

        raw_impact = ""
        if impact_cell:
            icon = impact_cell.select_one("span")
            if icon:
                classes = icon.get("class", [])
                if any("high" in c for c in classes):
                    raw_impact = "high"
                elif any("medium" in c for c in classes):
                    raw_impact = "medium"
                else:
                    raw_impact = "low"

        time_text = time_cell.get_text(strip=True) if time_cell else ""
        ts = _parse_ff_datetime(current_date, time_text)

        events.append(
            CalendarEvent(
                event_name=event_name,
                currency=currency,
                impact=_classify_impact(event_name, raw_impact),
                forecast=_cell_text(forecast_cell),
                previous=_cell_text(previous_cell),
                actual=_cell_text(actual_cell),
                timestamp=ts,
            )
        )

    log.info("calendar_html_parsed", count=len(events))
    return events


def _cell_text(cell: Any) -> str | None:
    if cell is None:
        return None
    t = cell.get_text(strip=True)
    return t if t else None


# ── HTML parser (investing.com) ──────────────────────────────────────────────

def _parse_investing_html(html_fragment: str) -> list[CalendarEvent]:
    """Parse the HTML fragment returned by investing.com's AJAX filter endpoint."""
    try:
        soup = BeautifulSoup(html_fragment, "html.parser")
    except Exception:
        log.error("calendar_investing_parse_failed")
        return []

    events: list[CalendarEvent] = []
    rows = soup.select("tr.js-event-item")

    for row in rows:
        event_cell = row.select_one("td.event")
        if not event_cell:
            continue
        link = event_cell.select_one("a")
        event_name = (link.get_text(strip=True) if link else event_cell.get_text(strip=True))
        if not event_name:
            continue

        currency_cell = row.select_one("td.flagCur")
        currency = ""
        if currency_cell:
            currency = currency_cell.get_text(strip=True).replace("\xa0", "").strip().upper()

        sentiment_cell = row.select_one("td.sentiment")
        raw_impact = ""
        if sentiment_cell:
            title = (sentiment_cell.get("title") or "").lower()
            if "high" in title:
                raw_impact = "high"
            elif "moderate" in title:
                raw_impact = "medium"
            elif "low" in title:
                raw_impact = "low"

        ts = _parse_investing_datetime(row.get("data-event-datetime", ""))

        actual_cell = row.select_one("td[class*='actual']")
        forecast_cell = row.select_one("td[class*='forecast']")
        previous_cell = row.select_one("td[class*='previous']")

        events.append(
            CalendarEvent(
                event_name=event_name,
                currency=currency,
                impact=_classify_impact(event_name, raw_impact),
                forecast=_cell_text(forecast_cell),
                previous=_cell_text(previous_cell),
                actual=_cell_text(actual_cell),
                timestamp=ts,
                source="investing.com",
            )
        )

    log.info("calendar_investing_parsed", count=len(events))
    return events


def _parse_investing_datetime(raw: str) -> datetime:
    """Parse investing.com's ``data-event-datetime="2026/07/05 06:00:00"`` attribute."""
    try:
        dt = datetime.strptime(raw, "%Y/%m/%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


# ── Multi-source merge ───────────────────────────────────────────────────────

_NAME_NOISE_RE = re.compile(r"[^a-z0-9]+")


def _normalize_event_name(name: str) -> str:
    return _NAME_NOISE_RE.sub("", name.lower())[:24]


def _dedup_key(e: CalendarEvent) -> tuple[str, str, str]:
    # Sources report timestamps in different timezones (FF uses US Eastern
    # offsets, investing.com uses UTC) — normalize to UTC before comparing
    # wall-clock components, otherwise identical events never match.
    ts_utc = e.timestamp.astimezone(timezone.utc) if e.timestamp.tzinfo else e.timestamp.replace(tzinfo=timezone.utc)
    return (e.currency, ts_utc.strftime("%Y-%m-%d %H:%M"), _normalize_event_name(e.event_name))


def _merge_events(
    primary: list[CalendarEvent], secondary: list[CalendarEvent],
) -> list[CalendarEvent]:
    """Merge calendar sources, de-duplicating events reported by both.

    ``primary`` (ForexFactory) wins on conflicts; ``secondary`` (investing.com)
    only contributes events not already covered, so coverage is additive.
    """
    seen: set[tuple[str, str, str]] = {_dedup_key(e) for e in primary}

    merged = list(primary)
    added = 0
    for e in secondary:
        key = _dedup_key(e)
        if key in seen:
            continue
        seen.add(key)
        merged.append(e)
        added += 1

    merged.sort(key=lambda e: e.timestamp)
    log.info(
        "calendar_merged", primary=len(primary), secondary=len(secondary),
        added_from_secondary=added, total=len(merged),
    )
    return merged


def _parse_calendar_ts(raw: str) -> datetime:
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%b %d, %Y %I:%M%p",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def _parse_ff_datetime(date_str: str, time_str: str) -> datetime:
    """Combine ForexFactory's split date/time into a UTC datetime."""
    now = datetime.now(timezone.utc)

    month_day = re.search(r"([A-Za-z]+)\s*(\d+)", date_str)
    if not month_day:
        return now

    month_name, day = month_day.group(1), int(month_day.group(2))
    month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    month = month_map.get(month_name[:3].lower(), now.month)

    hour, minute = 0, 0
    time_match = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", time_str, re.I)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        ampm = (time_match.group(3) or "").lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0

    try:
        return datetime(now.year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return now
