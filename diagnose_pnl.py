"""
diagnose_pnl.py — P&L diagnostic breakdown by session, regime, and conviction bucket.

Usage:
    python diagnose_pnl.py [--min-trades 5] [--top 10]

Reads from Redis:
    nexus:trade_memory:trades   — rolling list of trade records
    nexus:trade_memory:metrics  — aggregated metrics snapshot
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import redis.asyncio as aioredis


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
_KEY_TRADES = "nexus:trade_memory:trades"
_KEY_METRICS = "nexus:trade_memory:metrics"


def _fmt_pct(v: float) -> str:
    return f"{v*100:.1f}%"


def _kelly(win_rate: float, avg_win: float, avg_loss: float) -> float:
    if avg_loss == 0 or win_rate <= 0:
        return 0.0
    b = avg_win / avg_loss
    return win_rate - (1 - win_rate) / b


def _print_table(title: str, rows: list[tuple], headers: list[str]) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")
    col_w = [max(len(h), max((len(str(r[i])) for r in rows), default=0)) + 2
             for i, h in enumerate(headers)]
    header_line = "  ".join(h.ljust(col_w[i]) for i, h in enumerate(headers))
    print(f"  {header_line}")
    print(f"  {'  '.join('-'*w for w in col_w)}")
    for row in rows:
        print("  " + "  ".join(str(row[i]).ljust(col_w[i]) for i in range(len(headers))))


async def main(min_trades: int = 5, top: int = 10) -> None:
    r = aioredis.from_url(REDIS_URL, decode_responses=True)

    # ── Load raw trades ────────────────────────────────────────────────────
    try:
        raw = await r.get(_KEY_TRADES)
        metrics_raw = await r.get(_KEY_METRICS)
    finally:
        await r.aclose()

    if not raw:
        print("No trade records found in Redis. Run the system and close some trades first.")
        sys.exit(0)

    trades: list[dict] = json.loads(raw)
    metrics: dict = json.loads(metrics_raw) if metrics_raw else {}

    print(f"\n{'═'*60}")
    print(f"  NEXUS TRADER — P&L DIAGNOSTIC")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  |  {len(trades)} trades loaded")
    print(f"{'═'*60}")

    # ── Overall ────────────────────────────────────────────────────────────
    overall = metrics.get("overall", {})
    if overall:
        wr = float(overall.get("win_rate", 0))
        net = float(overall.get("net_pnl_usd", 0))
        avg_w = float(overall.get("avg_win_pips", 0))
        avg_l = float(overall.get("avg_loss_pips", 0))
        pf = float(overall.get("profit_factor", 0))
        kelly = _kelly(wr, avg_w, abs(avg_l))
        print(f"\n  OVERALL  WR={_fmt_pct(wr)}  Net=${net:.2f}  PF={pf:.2f}  Kelly={kelly:+.2f}")

    # ── By session ─────────────────────────────────────────────────────────
    by_session: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_session[t.get("session", "UNKNOWN")].append(t)

    session_rows = []
    for sess, ts in sorted(by_session.items()):
        if len(ts) < min_trades:
            continue
        wins = sum(1 for t in ts if t.get("is_win"))
        wr = wins / len(ts)
        net = sum(t.get("pnl_usd", 0) for t in ts)
        avg_w_pips = (sum(t.get("pnl_pips", 0) for t in ts if t.get("is_win")) / max(wins, 1))
        avg_l_pips = abs(sum(t.get("pnl_pips", 0) for t in ts if not t.get("is_win")) / max(len(ts) - wins, 1))
        kelly = _kelly(wr, avg_w_pips, avg_l_pips)
        pf = (avg_w_pips * wins) / max(avg_l_pips * (len(ts) - wins), 0.001)
        flag = " ⚠ NEGATIVE KELLY" if kelly < 0 else (" ✓" if wr >= 0.5 else "")
        session_rows.append((
            sess, len(ts), f"{_fmt_pct(wr)}", f"${net:.2f}",
            f"{pf:.2f}", f"{kelly:+.2f}", flag,
        ))

    if session_rows:
        _print_table(
            "BY SESSION",
            sorted(session_rows, key=lambda r: float(r[5].replace("+", "")), reverse=True),
            ["Session", "Trades", "Win%", "Net P&L", "PF", "Kelly", ""],
        )

    # ── By regime ──────────────────────────────────────────────────────────
    by_regime: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_regime[t.get("regime", "UNKNOWN")].append(t)

    regime_rows = []
    for reg, ts in sorted(by_regime.items()):
        if len(ts) < min_trades:
            continue
        wins = sum(1 for t in ts if t.get("is_win"))
        wr = wins / len(ts)
        net = sum(t.get("pnl_usd", 0) for t in ts)
        flag = " ✓" if wr >= 0.5 else (" ⚠ LOW WR" if wr < 0.35 else "")
        regime_rows.append((reg, len(ts), _fmt_pct(wr), f"${net:.2f}", flag))

    if regime_rows:
        _print_table(
            "BY REGIME",
            sorted(regime_rows, key=lambda r: r[2], reverse=True),
            ["Regime", "Trades", "Win%", "Net P&L", ""],
        )

    # ── By conviction bucket ───────────────────────────────────────────────
    buckets: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        c = float(t.get("conviction", 0))
        if c >= 0.90:
            b = "0.90+"
        elif c >= 0.85:
            b = "0.85-0.90"
        elif c >= 0.80:
            b = "0.80-0.85"
        elif c >= 0.75:
            b = "0.75-0.80"
        elif c >= 0.70:
            b = "0.70-0.75"
        else:
            b = "<0.70"
        buckets[b].append(t)

    bucket_rows = []
    for bkt in ["0.90+", "0.85-0.90", "0.80-0.85", "0.75-0.80", "0.70-0.75", "<0.70"]:
        ts = buckets.get(bkt, [])
        if len(ts) < 1:
            continue
        wins = sum(1 for t in ts if t.get("is_win"))
        wr = wins / len(ts) if ts else 0
        net = sum(t.get("pnl_usd", 0) for t in ts)
        bucket_rows.append((bkt, len(ts), _fmt_pct(wr), f"${net:.2f}"))

    if bucket_rows:
        _print_table("BY CONVICTION BUCKET", bucket_rows, ["Conviction", "Trades", "Win%", "Net P&L"])

    # ── Top losers (sessions with negative P&L) ───────────────────────────
    print(f"\n{'─'*60}")
    print("  WORST 5 TRADES (P&L USD)")
    print(f"{'─'*60}")
    worst = sorted(trades, key=lambda t: t.get("pnl_usd", 0))[:5]
    for t in worst:
        print(f"  [{t.get('closed_at','?')[:16]}]  {t.get('symbol','')} {t.get('side','')}  "
              f"session={t.get('session','')}  regime={t.get('regime','')}  "
              f"conviction={t.get('conviction',0):.2f}  pnl=${t.get('pnl_usd',0):.2f}")

    print(f"\n{'═'*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nexus Trader P&L Diagnostic")
    parser.add_argument("--min-trades", type=int, default=5,
                        help="Minimum trades per slice to display (default=5)")
    parser.add_argument("--top", type=int, default=10,
                        help="Top N rows per table (default=10)")
    args = parser.parse_args()
    asyncio.run(main(min_trades=args.min_trades, top=args.top))
