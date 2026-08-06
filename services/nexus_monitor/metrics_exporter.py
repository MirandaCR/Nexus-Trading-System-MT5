"""
Nexus Monitor — Prometheus metrics exporter.

Exposes trading metrics in Prometheus text format so Prometheus can
scrape the /metrics endpoint.
"""
from __future__ import annotations

import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class MetricsExporter:
    """Collects and exports trading metrics in Prometheus exposition format."""

    def __init__(self) -> None:
        self._counters: dict[str, float] = {
            "nexus_trades_total": 0,
            "nexus_trades_won": 0,
            "nexus_trades_lost": 0,
            "nexus_errors_total": 0,
            "nexus_agent_invocations_total": 0,
        }
        self._gauges: dict[str, float] = {
            "nexus_pnl_total_usd": 0.0,
            "nexus_win_rate": 0.0,
            "nexus_open_positions": 0,
            "nexus_profit_factor": 0.0,
            "nexus_sharpe_ratio": 0.0,
            "nexus_max_drawdown_pct": 0.0,
            "nexus_daily_pnl_usd": 0.0,
            "nexus_balance_usd": 0.0,
            "nexus_equity_usd": 0.0,
        }
        self._service_latencies: dict[str, float] = {}
        self._last_update = time.monotonic()

    # ── Counter Updates ───────────────────────────────────────────────────

    def inc_counter(self, name: str, value: float = 1.0) -> None:
        if name in self._counters:
            self._counters[name] += value
        else:
            self._counters[name] = value

    def set_gauge(self, name: str, value: float) -> None:
        self._gauges[name] = value

    # ── Trade Metrics ─────────────────────────────────────────────────────

    def update_trade_metrics(self, trade: dict[str, Any]) -> None:
        self._counters["nexus_trades_total"] += 1
        pnl = trade.get("pnl_usd", 0)

        if pnl > 0:
            self._counters["nexus_trades_won"] += 1
        else:
            self._counters["nexus_trades_lost"] += 1

        self._gauges["nexus_pnl_total_usd"] += pnl
        self._gauges["nexus_daily_pnl_usd"] += pnl

        total = self._counters["nexus_trades_total"]
        wins = self._counters["nexus_trades_won"]
        self._gauges["nexus_win_rate"] = (wins / total * 100) if total > 0 else 0

        self._last_update = time.monotonic()
        log.debug("metrics.trade_updated", total_trades=total, pnl=pnl)

    # ── Service Health Metrics ────────────────────────────────────────────

    def update_service_metrics(self, service_health: dict[str, Any]) -> None:
        service = service_health.get("service_name", "unknown")
        latency = service_health.get("latency_ms", 0)
        self._service_latencies[service] = latency
        self._last_update = time.monotonic()

    # ── Bulk State Update ─────────────────────────────────────────────────

    def update_state(
        self,
        *,
        open_positions: int | None = None,
        balance: float | None = None,
        equity: float | None = None,
        profit_factor: float | None = None,
        sharpe_ratio: float | None = None,
        max_drawdown: float | None = None,
    ) -> None:
        if open_positions is not None:
            self._gauges["nexus_open_positions"] = open_positions
        if balance is not None:
            self._gauges["nexus_balance_usd"] = balance
        if equity is not None:
            self._gauges["nexus_equity_usd"] = equity
        if profit_factor is not None:
            self._gauges["nexus_profit_factor"] = profit_factor
        if sharpe_ratio is not None:
            self._gauges["nexus_sharpe_ratio"] = sharpe_ratio
        if max_drawdown is not None:
            self._gauges["nexus_max_drawdown_pct"] = max_drawdown

    def reset_daily(self) -> None:
        self._gauges["nexus_daily_pnl_usd"] = 0.0
        log.info("metrics.daily_reset")

    # ── Prometheus Export ─────────────────────────────────────────────────

    def export_metrics(self) -> str:
        lines: list[str] = []

        lines.append("# HELP nexus_trades_total Total number of trades executed")
        lines.append("# TYPE nexus_trades_total counter")
        for name, value in sorted(self._counters.items()):
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")

        lines.append("")
        for name, value in sorted(self._gauges.items()):
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")

        lines.append("")
        for service, latency in sorted(self._service_latencies.items()):
            safe_name = service.replace("-", "_")
            lines.append(f'nexus_service_latency_ms{{service="{service}"}} {latency}')

        lines.append("")
        return "\n".join(lines) + "\n"

    def get_metrics_dict(self) -> dict[str, Any]:
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "service_latencies": dict(self._service_latencies),
            "last_update": self._last_update,
        }
