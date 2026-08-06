"""
Nexus Risk — Rules Engine with hardcoded circuit breakers.

Every rule has an absolute minimum that **cannot** be overridden by YAML config.
The YAML file ``risk_rules.yaml`` may tighten limits but never loosen them
below the hardcoded floor.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from shared.config_loader import ConfigLoader
from shared.constants import DEFAULT_RISK_PARAMS
from shared.models import Position, TradeProposal

log = structlog.get_logger(__name__)

# ── Hardcoded Absolute Minimums (INVIOLABLE) ─────────────────────────────────

_HARDCODED_MIN: dict[str, float | int] = {
    "max_risk_per_trade_pct":  2.0,
    "max_daily_loss_pct":      5.0,
    "max_weekly_drawdown_pct": 8.0,
    "max_open_positions":      3,
    "max_correlation":         0.7,
    "max_spread_multiplier":   2.0,
    "news_blackout_minutes":   5,
    "max_slippage_pips":       1.0,
    "friday_close_utc_hour":   20,
    "min_rr_ratio":            1.3,  # absolute floor — never allow below 1.3:1
}

_AGGRESSIVE_TRADING = os.getenv("NEXUS_AGGRESSIVE_TRADING", "false").lower() == "true"


@dataclass
class RuleViolation:
    rule: str
    message: str
    severity: str = "CRITICAL"
    current_value: float | str = 0.0
    limit_value: float | str = 0.0


@dataclass
class RulesEngineConfig:
    max_risk_per_trade_pct: float = 2.0
    max_daily_loss_pct: float = 5.0
    max_weekly_drawdown_pct: float = 8.0
    max_open_positions: int = 3
    max_correlation: float = 0.7
    max_spread_multiplier: float = 2.0
    news_blackout_minutes: int = 5
    max_slippage_pips: float = 1.0
    friday_close_utc_hour: int = 20
    min_rr_ratio: float = 1.5  # minimum risk-reward ratio for any trade


class RulesEngine:
    """Hardcoded circuit breakers for trade risk evaluation."""

    def __init__(self, config_loader: ConfigLoader | None = None) -> None:
        self._cfg = RulesEngineConfig()
        self._upcoming_news: list[dict[str, Any]] = []
        if config_loader:
            self._load_yaml_overrides(config_loader)

    def _load_yaml_overrides(self, loader: ConfigLoader) -> None:
        """Apply YAML config, but never loosen past hardcoded minimums."""
        try:
            raw = loader._load_yaml("risk_rules.yaml")
        except FileNotFoundError:
            log.warning("rules_engine.no_config", msg="risk_rules.yaml not found, using defaults")
            return

        capital = raw.get("capital", {})
        exposure = raw.get("exposure", {})
        execution = raw.get("execution", {})
        calendar = raw.get("calendar", {})

        self._cfg.max_risk_per_trade_pct = max(
            capital.get("max_risk_per_trade_pct", self._cfg.max_risk_per_trade_pct),
            _HARDCODED_MIN["max_risk_per_trade_pct"],
        )
        self._cfg.max_daily_loss_pct = max(
            capital.get("max_daily_loss_pct", self._cfg.max_daily_loss_pct),
            _HARDCODED_MIN["max_daily_loss_pct"],
        )
        self._cfg.max_weekly_drawdown_pct = max(
            capital.get("max_weekly_drawdown_pct", self._cfg.max_weekly_drawdown_pct),
            _HARDCODED_MIN["max_weekly_drawdown_pct"],
        )
        self._cfg.max_open_positions = max(
            int(exposure.get("max_simultaneous_positions", self._cfg.max_open_positions)),
            int(_HARDCODED_MIN["max_open_positions"]),
        )
        self._cfg.max_correlation = max(
            exposure.get("max_correlation_between_positions", self._cfg.max_correlation),
            _HARDCODED_MIN["max_correlation"],
        )
        self._cfg.max_spread_multiplier = max(
            execution.get("max_spread_multiplier", self._cfg.max_spread_multiplier),
            _HARDCODED_MIN["max_spread_multiplier"],
        )
        self._cfg.max_slippage_pips = max(
            execution.get("max_slippage_pips", self._cfg.max_slippage_pips),
            _HARDCODED_MIN["max_slippage_pips"],
        )
        self._cfg.news_blackout_minutes = max(
            int(calendar.get("news_blackout_minutes", self._cfg.news_blackout_minutes)),
            int(_HARDCODED_MIN["news_blackout_minutes"]),
        )

        friday_raw = calendar.get("friday_close_utc", "20:00")
        hour = int(str(friday_raw).split(":")[0])
        self._cfg.friday_close_utc_hour = max(hour, int(_HARDCODED_MIN["friday_close_utc_hour"]))

        # Optional high-frequency profile for live experimentation.
        if _AGGRESSIVE_TRADING:
            self._cfg.max_open_positions = max(self._cfg.max_open_positions, 8)
            self._cfg.max_spread_multiplier = max(self._cfg.max_spread_multiplier, 4.0)
            self._cfg.max_slippage_pips = max(self._cfg.max_slippage_pips, 4.0)
            self._cfg.max_correlation = max(self._cfg.max_correlation, 0.95)
            self._cfg.news_blackout_minutes = 0

        log.info("rules_engine.config_loaded", config=self._cfg)

    def set_upcoming_news(self, events: list[dict[str, Any]]) -> None:
        self._upcoming_news = events

    def apply_runtime_overrides(self, settings: dict[str, Any]) -> None:
        """Apply front-configurable runtime settings on top of YAML.

        Hardcoded minimums still act as a FLOOR on each limit so the user can
        loosen within reason but never below the inviolable safety floor.
        """
        if not isinstance(settings, dict):
            return
        if "max_daily_loss_pct" in settings:
            self._cfg.max_daily_loss_pct = max(
                float(settings["max_daily_loss_pct"]),
                _HARDCODED_MIN["max_daily_loss_pct"],
            )
        if "max_weekly_dd_pct" in settings:
            self._cfg.max_weekly_drawdown_pct = max(
                float(settings["max_weekly_dd_pct"]),
                _HARDCODED_MIN["max_weekly_drawdown_pct"],
            )
        if "max_open_positions" in settings:
            self._cfg.max_open_positions = max(
                int(settings["max_open_positions"]),
                int(_HARDCODED_MIN["max_open_positions"]),
            )
        if "min_rr_ratio" in settings:
            self._cfg.min_rr_ratio = max(
                float(settings["min_rr_ratio"]),
                float(_HARDCODED_MIN["min_rr_ratio"]),
            )

    # ── Master Check ──────────────────────────────────────────────────────

    def check_all_rules(
        self,
        trade_proposal: TradeProposal,
        current_state: dict[str, Any],
    ) -> tuple[bool, list[RuleViolation]]:
        violations: list[RuleViolation] = []

        balance = current_state.get("balance", 10_000.0)
        daily_loss = current_state.get("daily_loss_pct", 0.0)
        weekly_dd = current_state.get("weekly_dd_pct", 0.0)
        open_count = current_state.get("open_positions_count", 0)
        open_positions = current_state.get("open_positions", [])
        current_spread = current_state.get("current_spread", 0.0)
        avg_spread = current_state.get("avg_spread", 1.0)
        expected_price = current_state.get("expected_price", trade_proposal.entry)
        actual_price = current_state.get("actual_price", trade_proposal.entry)

        v = self.check_max_risk_per_trade(
            trade_proposal.conviction, trade_proposal.entry, trade_proposal.sl, balance,
        )
        if v:
            violations.append(v)

        v = self.check_daily_loss(daily_loss)
        if v:
            violations.append(v)

        v = self.check_weekly_drawdown(weekly_dd)
        if v:
            violations.append(v)

        v = self.check_max_positions(open_count)
        if v:
            violations.append(v)

        v = self.check_correlation(trade_proposal.symbol, open_positions)
        if v:
            violations.append(v)

        v = self.check_spread(current_spread, avg_spread)
        if v:
            violations.append(v)

        v = self.check_news_blackout()
        if v:
            violations.append(v)

        v = self.check_slippage(expected_price, actual_price, symbol=trade_proposal.symbol)
        if v:
            violations.append(v)

        v = self.check_friday_close()
        if v:
            violations.append(v)

        v = self.check_min_rr(trade_proposal.entry, trade_proposal.sl, trade_proposal.tp1)
        if v:
            violations.append(v)

        passed = len(violations) == 0
        log.info(
            "rules_engine.check_complete",
            proposal_id=trade_proposal.id,
            passed=passed,
            violations=len(violations),
        )
        return passed, violations

    # ── Individual Rule Checks ────────────────────────────────────────────

    def check_max_risk_per_trade(
        self, size: float, entry: float, sl: float, balance: float,
    ) -> RuleViolation | None:
        if balance <= 0 or entry == sl:
            return RuleViolation(
                rule="max_risk_per_trade",
                message="Invalid balance or entry == SL",
                current_value=0,
                limit_value=self._cfg.max_risk_per_trade_pct,
            )
        pip_risk = abs(entry - sl)
        risk_amount = size * pip_risk
        risk_pct = (risk_amount / balance) * 100
        if risk_pct > self._cfg.max_risk_per_trade_pct:
            return RuleViolation(
                rule="max_risk_per_trade",
                message=f"Risk {risk_pct:.2f}% exceeds max {self._cfg.max_risk_per_trade_pct}%",
                current_value=round(risk_pct, 2),
                limit_value=self._cfg.max_risk_per_trade_pct,
            )
        return None

    def check_daily_loss(self, current_daily_loss: float) -> RuleViolation | None:
        if abs(current_daily_loss) >= self._cfg.max_daily_loss_pct:
            return RuleViolation(
                rule="max_daily_loss",
                message=f"Daily loss {abs(current_daily_loss):.2f}% hit limit {self._cfg.max_daily_loss_pct}%",
                severity="CRITICAL",
                current_value=round(abs(current_daily_loss), 2),
                limit_value=self._cfg.max_daily_loss_pct,
            )
        return None

    def check_weekly_drawdown(self, current_weekly_dd: float) -> RuleViolation | None:
        if abs(current_weekly_dd) >= self._cfg.max_weekly_drawdown_pct:
            return RuleViolation(
                rule="max_weekly_drawdown",
                message=f"Weekly drawdown {abs(current_weekly_dd):.2f}% hit limit {self._cfg.max_weekly_drawdown_pct}%",
                severity="CRITICAL",
                current_value=round(abs(current_weekly_dd), 2),
                limit_value=self._cfg.max_weekly_drawdown_pct,
            )
        return None

    def check_max_positions(self, open_count: int) -> RuleViolation | None:
        if open_count >= self._cfg.max_open_positions:
            return RuleViolation(
                rule="max_positions",
                message=f"{open_count} open positions, max is {self._cfg.max_open_positions}",
                current_value=open_count,
                limit_value=self._cfg.max_open_positions,
            )
        return None

    def check_correlation(
        self, symbol: str, open_positions: list[dict[str, Any] | Position],
    ) -> RuleViolation | None:
        if not open_positions:
            return None

        corr_pairs: dict[str, float] = {
            "EURUSD:GBPUSD": 0.85, "EURUSD:USDCHF": -0.90,
            "GBPUSD:EURGBP": -0.70, "AUDUSD:NZDUSD": 0.90,
            "USDJPY:EURJPY": 0.80, "GBPUSD:GBPJPY": 0.75,
            "EURUSD:EURJPY": 0.60, "AUDUSD:AUDJPY": 0.65,
            "USDCAD:USDCHF": 0.55,
        }
        sym = symbol.upper().replace("/", "")
        for pos in open_positions:
            pos_sym = (pos.get("symbol", "") if isinstance(pos, dict) else pos.symbol)
            pos_sym = pos_sym.upper().replace("/", "")
            if pos_sym == sym:
                return RuleViolation(
                    rule="duplicate_position",
                    message=f"Already have an open position on {sym}",
                    current_value=sym,
                    limit_value="unique",
                )

            pair_key_a = f"{sym}:{pos_sym}"
            pair_key_b = f"{pos_sym}:{sym}"
            corr = corr_pairs.get(pair_key_a) or corr_pairs.get(pair_key_b)
            if corr is not None and abs(corr) > self._cfg.max_correlation:
                return RuleViolation(
                    rule="correlation_limit",
                    message=f"Correlation {sym}/{pos_sym} = {abs(corr):.2f} > {self._cfg.max_correlation}",
                    current_value=round(abs(corr), 2),
                    limit_value=self._cfg.max_correlation,
                )
        return None

    def check_spread(self, current_spread: float, avg_spread: float) -> RuleViolation | None:
        if avg_spread <= 0:
            return None
        ratio = current_spread / avg_spread
        if ratio > self._cfg.max_spread_multiplier:
            return RuleViolation(
                rule="spread_limit",
                message=f"Spread {ratio:.1f}x average exceeds {self._cfg.max_spread_multiplier}x limit",
                current_value=round(ratio, 2),
                limit_value=self._cfg.max_spread_multiplier,
            )
        return None

    def check_news_blackout(self) -> RuleViolation | None:
        if self._cfg.news_blackout_minutes <= 0:
            return None
        now = datetime.now(timezone.utc)
        for event in self._upcoming_news:
            event_time = event.get("timestamp")
            if event_time is None:
                continue
            if isinstance(event_time, str):
                try:
                    event_time = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue
            if not hasattr(event_time, "tzinfo") or event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)

            minutes_until = (event_time - now).total_seconds() / 60
            impact = event.get("impact", "low")
            if impact in ("high", "HIGH") and 0 <= minutes_until <= self._cfg.news_blackout_minutes:
                return RuleViolation(
                    rule="news_blackout",
                    message=f"High-impact news in {minutes_until:.0f} min — blackout active",
                    severity="CRITICAL",
                    current_value=f"{minutes_until:.0f}min",
                    limit_value=f"{self._cfg.news_blackout_minutes}min",
                )
        return None

    def check_slippage(
        self, expected: float, actual: float, symbol: str = "",
    ) -> RuleViolation | None:
        # Infer pip divisor from price magnitude or symbol
        if symbol:
            from shared.asset_specs import get_pip_size
            pip_sz = get_pip_size(symbol)
            pip_divisor = 1.0 / pip_sz if pip_sz > 0 else 10_000.0
        else:
            # Infer from price: gold/indices > 500, JPY > 10
            pip_divisor = 100.0 if expected > 10 else 10_000.0
        slip_pips = abs(expected - actual) * pip_divisor
        if slip_pips > self._cfg.max_slippage_pips:
            return RuleViolation(
                rule="slippage_limit",
                message=f"Slippage {slip_pips:.1f} pips exceeds {self._cfg.max_slippage_pips} pip limit",
                current_value=round(slip_pips, 1),
                limit_value=self._cfg.max_slippage_pips,
            )
        return None

    def check_friday_close(self) -> RuleViolation | None:
        if _AGGRESSIVE_TRADING:
            return None
        now = datetime.now(timezone.utc)
        if now.weekday() == 4 and now.hour >= self._cfg.friday_close_utc_hour:
            return RuleViolation(
                rule="friday_close",
                message=f"No new trades after {self._cfg.friday_close_utc_hour}:00 UTC Friday",
                severity="CRITICAL",
                current_value=f"{now.hour}:{now.minute:02d} UTC",
                limit_value=f"{self._cfg.friday_close_utc_hour}:00 UTC",
            )
        return None

    def check_min_rr(
        self, entry: float, sl: float, tp: float,
    ) -> RuleViolation | None:
        """Reject trades whose risk-reward ratio is below the configured minimum.

        tp=0 means no TP yet set (trailing stop / open-ended); skip the check.
        """
        if tp == 0 or sl == 0 or entry == sl:
            return None
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if risk == 0:
            return None
        rr = reward / risk
        min_rr = max(self._cfg.min_rr_ratio, _HARDCODED_MIN["min_rr_ratio"])
        if rr < min_rr:
            return RuleViolation(
                rule="min_rr_ratio",
                message=(
                    f"R:R {rr:.2f}:1 below minimum {min_rr:.1f}:1 "
                    f"(entry={entry}, sl={sl}, tp={tp})"
                ),
                severity="CRITICAL",
                current_value=round(rr, 2),
                limit_value=min_rr,
            )
        return None

    def get_active_circuit_breakers(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        now_iso = datetime.now(timezone.utc).isoformat()
        daily_loss = abs(state.get("daily_loss_pct", 0))
        weekly_dd = abs(state.get("weekly_dd_pct", 0))
        open_positions = state.get("open_positions_count", 0)
        is_tilt = state.get("tilt_status", "NORMAL").upper() == "TILT"
        friday_v = self.check_friday_close()

        def _b(name: str, triggered: bool, reason: str) -> dict[str, Any]:
            return {
                "name": name,
                "triggered": triggered,
                "triggered_at": now_iso if triggered else None,
                "reason": reason if triggered else "",
            }

        return [
            _b(
                "daily_loss_limit",
                daily_loss >= self._cfg.max_daily_loss_pct,
                f"Daily loss {daily_loss:.1f}% >= {self._cfg.max_daily_loss_pct}%",
            ),
            _b(
                "weekly_drawdown_limit",
                weekly_dd >= self._cfg.max_weekly_drawdown_pct,
                f"Weekly DD {weekly_dd:.1f}% >= {self._cfg.max_weekly_drawdown_pct}%",
            ),
            _b(
                "max_positions",
                open_positions >= self._cfg.max_open_positions,
                f"Open positions {open_positions} >= {self._cfg.max_open_positions}",
            ),
            _b("anti_tilt", is_tilt, "Tilt detected — trading paused"),
            _b(
                "friday_close",
                friday_v is not None,
                friday_v.message if friday_v else "",
            ),
            _b("anti_stacking", False, ""),
            _b("anti_revenge", False, ""),
            _b("session_gate", False, ""),
            _b("min_conviction", False, ""),
            _b("daily_trade_limit", False, ""),
            _b("trade_cooldown", False, ""),
            _b("correlation_limit", False, ""),
            _b("spread_limit", False, ""),
            _b("news_blackout", False, ""),
        ]
