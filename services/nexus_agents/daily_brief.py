"""
Daily Brief Service — Fase 4

Generates a structured macro brief for gold trading at 06:00 UTC and 12:00 UTC.
Reads cross-asset context, regime, signal quality, and calendar events from Redis.
Calls DeepSeek via LangChain and stores the result in nexus:genai:daily_bias.

Redis keys written:
  nexus:genai:daily_bias          — full structured brief (read by GenAIBrief frontend)
  nexus:genai:brief:last_run      — ISO timestamp of last successful run
  nexus:genai:brief:status        — "running" / "ok" / "error"
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── System-wide alert publisher ───────────────────────────────────────────────
_SYS_ALERT_LOG = "nexus:alerts:log"
_SYS_ALERT_PUB = "nexus:alerts:pubsub"


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


BRIEF_REDIS_KEY = "nexus:genai:daily_bias"
BRIEF_STATUS_KEY = "nexus:genai:brief:status"
BRIEF_LAST_RUN_KEY = "nexus:genai:brief:last_run"
BRIEF_TTL_SEC = 8 * 3600   # keep for 8h even if service restarts

# Run at 06:00 and 13:00 UTC (NY pre-open)
BRIEF_HOURS_UTC = {6, 13}


def _build_llm():
    """Instantiate the configured provider's LLM (deepseek | openai | gemini | local).

    Provider is selected with ``NEXUS_LLM_PROVIDER`` via shared.llm_factory.
    ``NEXUS_BRIEF_MODEL_CLOUD`` still overrides the model on DeepSeek.
    """
    from shared.llm_factory import make_llm, resolve_provider

    provider = resolve_provider()
    model = os.getenv("NEXUS_BRIEF_MODEL_CLOUD") if provider == "deepseek" else None
    return make_llm(temperature=0.3, max_tokens=1200, model=model, request_timeout=45)


async def _gather_context(r) -> dict:
    """Pull all context from Redis needed to generate the brief."""
    ctx: dict = {}

    # Cross-asset summary
    try:
        raw = await r.get("nexus:cross_asset:summary")
        if raw:
            ctx["cross_asset"] = json.loads(raw)
    except Exception:
        pass

    # Current regime
    try:
        raw = await r.get("nexus:regime:current")
        if raw:
            ctx["regime"] = json.loads(raw)
    except Exception:
        pass

    # Signal quality last
    try:
        raw = await r.get("nexus:signal:quality:last")
        if raw:
            ctx["signal_quality"] = json.loads(raw)
    except Exception:
        pass

    # TradeMemory metrics (win rate, recent streak)
    try:
        raw = await r.get("nexus:trade_memory:metrics")
        if raw:
            m = json.loads(raw)
            ctx["trade_memory"] = {
                "overall_wr": m.get("overall_win_rate"),
                "total_trades": m.get("total_trades"),
                "recent_streak": m.get("recent_streak", {}),
            }
    except Exception:
        pass

    # News sentinel alerts (last 5)
    try:
        raw = await r.lrange("nexus:news:sentinel:alerts", 0, 4)
        if raw:
            ctx["news_alerts"] = [json.loads(x) for x in raw]
    except Exception:
        pass

    return ctx


def _build_prompt(ctx: dict, symbol: str, session: str) -> str:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ca = ctx.get("cross_asset", {})
    regime = ctx.get("regime", {})
    sq = ctx.get("signal_quality", {})
    tm = ctx.get("trade_memory", {})
    news = ctx.get("news_alerts", [])

    dxy = ca.get("dxy", {})
    us10y = ca.get("us10y", {})
    gold_regime = ca.get("gold_yield_regime", "unknown")
    divg = ca.get("gold_dxy_divergence", 0)

    news_summary = ""
    if news:
        news_summary = "\nRecent high-impact alerts:\n" + "\n".join(
            f"  - [{a.get('impact','?')}] {a.get('title', a.get('headline', '?'))}"
            for a in news[:5]
        )
    else:
        news_summary = "\nNo high-impact news alerts in the last 4 hours."

    streak = tm.get("recent_streak", {})
    streak_str = f"{streak.get('direction','?')} ×{streak.get('count',0)}" if streak else "none"

    return f"""You are the Nexus Trader macro analyst generating a structured daily trading brief for {symbol}.
Time: {now_utc}  |  Session: {session}

=== CROSS-ASSET CONTEXT ===
DXY (Dollar Index): level={dxy.get('level','?')}  change_5d={dxy.get('change_5d','?')}%
US10Y Yield: level={us10y.get('level','?')}%  change_5d={us10y.get('change_5d','?')}pp
Gold-Yield Regime: {gold_regime.upper()}
Gold/DXY Divergence: {'+1 (normal inverse)' if divg==1 else '-1 (diverging — caution)' if divg==-1 else 'neutral'}
{news_summary}

=== MARKET REGIME ===
Regime: {regime.get('regime','UNKNOWN')}
Volatility: {regime.get('volatility_label','?')}
HMM Confidence: {regime.get('hmm_confidence','?')}

=== SYSTEM PERFORMANCE ===
Overall WR: {tm.get('overall_wr','?')}  Trades: {tm.get('total_trades','?')}
Recent streak: {streak_str}
Signal quality score: {sq.get('score','?')}/{sq.get('max_score',100)}

Based on this context, generate a concise trading brief. Respond ONLY with a JSON object with these exact keys:
{{
  "bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "summary": "<2-3 sentence macro outlook for gold today>",
  "key_drivers": ["<driver 1>", "<driver 2>", "<driver 3>"],
  "risk_events": ["<risk 1>", "<risk 2>"],
  "session_outlook": {{
    "LONDON": "<1 sentence>",
    "NEW_YORK": "<1 sentence>"
  }},
  "dxy_signal": "HEADWIND" | "TAILWIND" | "NEUTRAL",
  "yield_signal": "HEADWIND" | "TAILWIND" | "NEUTRAL",
  "confidence": <0-100 integer>
}}
"""


async def generate_brief(r, symbol: str = "XAUUSD") -> dict:
    """Generate a macro brief and write it to Redis. Returns the brief dict."""
    now_utc = datetime.now(timezone.utc)
    session = "LONDON" if 7 <= now_utc.hour < 13 else "NEW_YORK" if 13 <= now_utc.hour < 21 else "TOKYO"

    await r.set(BRIEF_STATUS_KEY, "running", ex=600)
    try:
        ctx = await _gather_context(r)
        prompt = _build_prompt(ctx, symbol, session)

        llm = _build_llm()
        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: llm.invoke([{"role": "user", "content": prompt}])
        )
        raw_text = response.content if hasattr(response, "content") else str(response)

        # Extract JSON from response
        import re
        json_match = re.search(r'\{[\s\S]*\}', raw_text)
        if not json_match:
            raise ValueError("No JSON found in LLM response")

        brief = json.loads(json_match.group())
        brief["symbol"] = symbol
        brief["generated_at"] = now_utc.isoformat()
        brief["session"] = session
        brief["cross_asset"] = ctx.get("cross_asset", {})

        await r.set(BRIEF_REDIS_KEY, json.dumps(brief), ex=BRIEF_TTL_SEC)
        await r.set(BRIEF_STATUS_KEY, "ok", ex=BRIEF_TTL_SEC)
        await r.set(BRIEF_LAST_RUN_KEY, now_utc.isoformat(), ex=BRIEF_TTL_SEC)
        logger.info("Daily brief generated: bias=%s confidence=%s", brief.get("bias"), brief.get("confidence"))

        await _publish_system_alert(
            r, "BRIEF_READY",
            f"Brief macro: {brief.get('bias','?')} — confianza {brief.get('confidence','?')}%",
            severity="INFO",
            bias=brief.get("bias"),
            confidence=brief.get("confidence"),
            symbol=symbol,
            session=session,
        )
        return brief

    except Exception as exc:
        logger.error("Daily brief generation failed: %s", exc, exc_info=True)
        await r.set(BRIEF_STATUS_KEY, "error", ex=3600)

        # Write a fallback brief so the frontend always has something
        fallback = {
            "symbol": symbol,
            "bias": "NEUTRAL",
            "summary": "Brief generation failed — using fallback. Check your AI provider API key (NEXUS_LLM_PROVIDER) and connectivity.",
            "key_drivers": [],
            "risk_events": [],
            "session_outlook": {"LONDON": "—", "NEW_YORK": "—"},
            "dxy_signal": "NEUTRAL",
            "yield_signal": "NEUTRAL",
            "confidence": 0,
            "generated_at": now_utc.isoformat(),
            "session": session,
            "error": str(exc),
        }
        await r.set(BRIEF_REDIS_KEY, json.dumps(fallback), ex=3600)
        return fallback


async def daily_brief_loop(r, symbols: list[str] | None = None) -> None:
    """Background loop: generate brief at 06:00 and 13:00 UTC every day.

    Also triggers immediately on startup if no brief exists yet.
    """
    syms = symbols or ["XAUUSD"]
    logger.info("Daily brief loop started. Scheduled hours UTC: %s", sorted(BRIEF_HOURS_UTC))

    # Startup: generate if no brief stored yet or brief is stale (>7h)
    try:
        existing = await r.get(BRIEF_REDIS_KEY)
        if existing:
            data = json.loads(existing)
            gen_at = data.get("generated_at", "")
            if gen_at:
                age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(gen_at)).total_seconds() / 3600
                if age_h < 7:
                    logger.info("Brief already fresh (%.1fh old) — skipping startup generation", age_h)
                    existing = None  # sentinel: still enter the loop
        if not existing:
            for sym in syms:
                await generate_brief(r, sym)
    except Exception:
        logger.warning("Startup brief generation failed", exc_info=True)

    _last_run_hour: int = -1

    while True:
        try:
            await asyncio.sleep(60)
            now = datetime.now(timezone.utc)
            if now.hour in BRIEF_HOURS_UTC and now.hour != _last_run_hour and now.minute < 5:
                _last_run_hour = now.hour
                logger.info("Scheduled brief generation at %02d:00 UTC", now.hour)
                for sym in syms:
                    await generate_brief(r, sym)
        except asyncio.CancelledError:
            logger.info("Daily brief loop cancelled")
            break
        except Exception:
            logger.error("Daily brief loop error", exc_info=True)
            await asyncio.sleep(300)
