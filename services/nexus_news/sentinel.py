"""
News Sentinel — Fase 4

Continuous background monitor that:
  1. Classifies every news item and calendar event as HIGH / MEDIUM / LOW impact
  2. Publishes structured alerts to Redis (nexus:news:sentinel:alerts, list, max 30)
  3. Detects "blackout windows" — ±BLACKOUT_MIN minutes around HIGH-impact events
  4. Publishes blackout status to nexus:news:sentinel:blackout

Redis keys written:
  nexus:news:sentinel:alerts          — JSON list of up to 30 recent alert dicts
  nexus:news:sentinel:blackout        — JSON {active, reason, ends_at, events[]}
  nexus:news:sentinel:last_scan       — ISO timestamp
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── System-wide alert publisher ───────────────────────────────────────────────
_SYS_ALERT_LOG = "nexus:alerts:log"
_SYS_ALERT_PUB = "nexus:alerts:pubsub"
_last_blackout_active: bool | None = None   # None = unknown (first scan ever)


async def _publish_system_alert(r, alert_type: str, message: str, severity: str = "INFO", **kw) -> None:
    payload = json.dumps({
        "type": alert_type, "message": message, "severity": severity,
        "ts": datetime.now(timezone.utc).isoformat(), **kw,
    })
    try:
        await r.lpush(_SYS_ALERT_LOG, payload)
        await r.ltrim(_SYS_ALERT_LOG, 0, 49)
        await r.expire(_SYS_ALERT_LOG, 86400)
        await r.publish(_SYS_ALERT_PUB, payload)
    except Exception as exc:
        logger.debug("_publish_system_alert failed: %s", exc)


ALERTS_KEY   = "nexus:news:sentinel:alerts"
BLACKOUT_KEY = "nexus:news:sentinel:blackout"
LAST_SCAN_KEY = "nexus:news:sentinel:last_scan"

ALERTS_TTL_SEC    = 24 * 3600
BLACKOUT_TTL_SEC  = 3600
MAX_ALERTS        = 30
BLACKOUT_MIN      = 30        # minutes before/after HIGH event to block trading
SENTINEL_POLL_SEC = 60        # re-scan every 60s

# Keywords that always trigger HIGH classification regardless of source
_HIGH_KWORDS = frozenset({
    "fomc", "fed ", "federal reserve", "interest rate", "rate decision",
    "nonfarm", "non-farm", "nfp", "payroll",
    "cpi", "inflation", "core cpi", "core inflation",
    "gdp", "gross domestic",
    "ecb", "bank of england", "boe", "boj", "bank of japan",
    "unemployment rate", "employment change",
    "monetary policy", "quantitative",
})

_MEDIUM_KWORDS = frozenset({
    "pmi", "ism", "retail sales", "trade balance",
    "consumer confidence", "durable goods", "housing",
    "building permits", "industrial production", "ppi",
})

_GOLD_KWORDS = frozenset({
    "gold", "xau", "precious metal", "safe haven",
    "dollar", "dxy", "treasury", "yield", "bond",
    "geopolit", "war", "conflict", "crisis", "sanction",
    "inflation", "cpi", "fed", "fomc", "rate",
})


def _classify_headline(text: str, raw_impact: str = "") -> str:
    """Return 'HIGH' | 'MEDIUM' | 'LOW' for a headline."""
    if raw_impact.lower() in ("high", "medium", "low"):
        return raw_impact.upper()
    lower = text.lower()
    if any(kw in lower for kw in _HIGH_KWORDS):
        return "HIGH"
    if any(kw in lower for kw in _MEDIUM_KWORDS):
        return "MEDIUM"
    return "LOW"


def _gold_relevance(text: str) -> bool:
    """True if the headline is relevant to gold/USD trading."""
    lower = text.lower()
    return any(kw in lower for kw in _GOLD_KWORDS)


def _alert_dict(
    title: str,
    impact: str,
    source: str,
    published_at: str | None = None,
    event_time: str | None = None,
    currency: str = "",
    gold_relevant: bool = False,
) -> dict[str, Any]:
    return {
        "title": title[:200],
        "impact": impact,
        "source": source,
        "currency": currency,
        "gold_relevant": gold_relevant,
        "published_at": published_at or datetime.now(timezone.utc).isoformat(),
        "event_time": event_time,
    }


async def _push_alert(r, alert: dict) -> None:
    """Prepend an alert to the sentinel list, capping at MAX_ALERTS."""
    try:
        await r.lpush(ALERTS_KEY, json.dumps(alert))
        await r.ltrim(ALERTS_KEY, 0, MAX_ALERTS - 1)
        await r.expire(ALERTS_KEY, ALERTS_TTL_SEC)
    except Exception:
        pass


async def _check_blackout(r, calendar_events: list[dict]) -> dict:
    """Compute whether we're in a blackout window for HIGH-impact events."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=BLACKOUT_MIN)
    window_end   = now + timedelta(minutes=BLACKOUT_MIN)

    active_events = []
    for ev in calendar_events:
        if ev.get("impact", "").upper() != "HIGH":
            continue
        raw_time = ev.get("event_time") or ev.get("datetime") or ev.get("timestamp")
        if not raw_time:
            continue
        try:
            ev_dt = datetime.fromisoformat(str(raw_time))
            if ev_dt.tzinfo is None:
                ev_dt = ev_dt.replace(tzinfo=timezone.utc)
            if window_start <= ev_dt <= window_end:
                active_events.append({
                    "title": ev.get("title", ev.get("name", "Unknown event")),
                    "event_time": ev_dt.isoformat(),
                    "currency": ev.get("currency", ""),
                })
        except Exception:
            continue

    blackout_active = len(active_events) > 0
    ends_at = None
    if blackout_active:
        last_ev_times = [datetime.fromisoformat(e["event_time"]) for e in active_events]
        ends_at = (max(last_ev_times) + timedelta(minutes=BLACKOUT_MIN)).isoformat()

    result = {
        "active": blackout_active,
        "reason": f"HIGH-impact event within ±{BLACKOUT_MIN}min" if blackout_active else "clear",
        "ends_at": ends_at,
        "events": active_events,
        "checked_at": now.isoformat(),
    }

    try:
        await r.set(BLACKOUT_KEY, json.dumps(result), ex=BLACKOUT_TTL_SEC)
    except Exception:
        pass

    return result


async def scan_once(r, fetcher=None, calendar=None) -> dict[str, Any]:
    """Run a single sentinel scan: fetch news + calendar, classify, push alerts.

    Returns {"alerts_added": int, "blackout": dict}
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    added = 0

    # ── News items ────────────────────────────────────────────────────────
    new_items: list[dict] = []
    if fetcher is not None:
        try:
            raw = await fetcher.fetch("XAUUSD")
            for item in raw[:20]:
                if isinstance(item, dict):
                    headline = item.get("headline", item.get("title", ""))
                elif hasattr(item, "headline"):
                    headline = item.headline
                else:
                    headline = str(item)

                impact = _classify_headline(headline)
                relevant = _gold_relevance(headline)

                if impact in ("HIGH", "MEDIUM") or relevant:
                    pub = None
                    for f in ("published_at", "timestamp", "datetime"):
                        val = item.get(f) if isinstance(item, dict) else getattr(item, f, None)
                        if val:
                            pub = str(val)
                            break
                    alert = _alert_dict(
                        title=headline,
                        impact=impact,
                        source="news_feed",
                        published_at=pub,
                        gold_relevant=relevant,
                    )
                    new_items.append(alert)
        except Exception as exc:
            logger.debug("sentinel: news fetch failed: %s", exc)

    # ── Calendar events ───────────────────────────────────────────────────
    cal_dicts: list[dict] = []
    if calendar is not None:
        try:
            events = await calendar.get_events(hours_ahead=24)
            for ev in events:
                if isinstance(ev, dict):
                    name = ev.get("name", ev.get("title", ""))
                    raw_impact = ev.get("impact", "")
                    ev_time = ev.get("datetime") or ev.get("timestamp")
                    currency = ev.get("currency", "")
                else:
                    name = getattr(ev, "name", str(ev))
                    raw_impact = getattr(ev, "impact", "")
                    ev_time = getattr(ev, "event_time", None)
                    currency = getattr(ev, "currency", "")

                impact = _classify_headline(name, raw_impact)
                relevant = currency in ("USD", "XAU") or _gold_relevance(name)
                ev_time_str = str(ev_time) if ev_time else None

                cal_entry = {
                    "title": name[:200],
                    "impact": impact,
                    "currency": currency,
                    "event_time": ev_time_str,
                    "gold_relevant": relevant,
                }
                cal_dicts.append(cal_entry)

                if impact == "HIGH" or (impact == "MEDIUM" and relevant):
                    alert = _alert_dict(
                        title=name,
                        impact=impact,
                        source="calendar",
                        event_time=ev_time_str,
                        currency=currency,
                        gold_relevant=relevant,
                    )
                    new_items.append(alert)
        except Exception as exc:
            logger.debug("sentinel: calendar fetch failed: %s", exc)

    # Push all new alerts (dedup by title in last 30min would be ideal but
    # lpush+ltrim is simpler and fast enough for this use case)
    for alert in new_items:
        await _push_alert(r, alert)
        added += 1

    # Blackout check
    blackout = await _check_blackout(r, cal_dicts)

    # Timestamp
    try:
        await r.set(LAST_SCAN_KEY, now.isoformat(), ex=3600)
    except Exception:
        pass

    # Publish blackout state transitions (avoid re-publishing same state)
    global _last_blackout_active
    if _last_blackout_active is not None:
        if blackout["active"] and not _last_blackout_active:
            events_str = ", ".join(e.get("title", "?") for e in blackout.get("events", [])[:2])
            await _publish_system_alert(
                r, "BLACKOUT_START",
                f"Blackout activo: {events_str or 'evento de alto impacto'}",
                severity="HIGH",
                events=blackout.get("events", []),
                ends_at=blackout.get("ends_at"),
            )
            logger.warning("NEWS SENTINEL: blackout active — %s", blackout["events"])
        elif not blackout["active"] and _last_blackout_active:
            await _publish_system_alert(
                r, "BLACKOUT_END",
                "Blackout despejado — trading reanudado",
                severity="INFO",
            )
            logger.info("NEWS SENTINEL: blackout cleared")
    _last_blackout_active = blackout["active"]

    if blackout["active"]:
        logger.debug("NEWS SENTINEL: blackout still active — %s", blackout["events"])

    return {"alerts_added": added, "blackout": blackout}


async def sentinel_loop(r, fetcher=None, calendar=None, poll_sec: int = SENTINEL_POLL_SEC) -> None:
    """Background loop: scan news and calendar every poll_sec seconds."""
    logger.info("News sentinel loop started (poll=%ds, blackout=±%dmin)", poll_sec, BLACKOUT_MIN)

    # Initial scan at startup
    try:
        result = await scan_once(r, fetcher, calendar)
        logger.info("Sentinel startup scan: alerts_added=%d blackout=%s",
                    result["alerts_added"], result["blackout"]["active"])
    except Exception:
        logger.warning("Sentinel startup scan failed", exc_info=True)

    while True:
        try:
            await asyncio.sleep(poll_sec)
            result = await scan_once(r, fetcher, calendar)
            if result["alerts_added"] > 0:
                logger.info("Sentinel scan: +%d alerts, blackout=%s",
                            result["alerts_added"], result["blackout"]["active"])
        except asyncio.CancelledError:
            logger.info("News sentinel loop cancelled")
            break
        except Exception:
            logger.error("Sentinel scan error", exc_info=True)
            await asyncio.sleep(60)
