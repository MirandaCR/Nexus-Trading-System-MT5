"""
Nexus Risk — Dynamic position sizing using Kelly Criterion.

Formula:
  Size = (Balance × Risk%) / |Entry − SL| × Vol_Factor × Tilt_Factor × Session_Factor

Kelly fraction is used to cap maximum risk percentage so the system never
over-allocates even when win-rate appears favourable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from shared.config_loader import ConfigLoader

log = structlog.get_logger(__name__)

# ── Default factor tables (mirrored from risk_rules.yaml) ────────────────────

_DEFAULT_VOL_FACTORS: dict[str, float] = {
    "low": 1.0,
    "medium": 0.75,
    "high": 0.5,
    "extreme": 0.25,
}

_DEFAULT_TILT_FACTORS: dict[str, float] = {
    "normal": 1.0,
    "warning": 0.5,
    "tilt": 0.0,
}

_DEFAULT_SESSION_FACTORS: dict[str, float] = {
    "off": 0.5,
    "off_hours": 0.5,
    "sydney": 0.5,
    "tokyo": 0.75,
    "london": 1.0,
    "new_york": 1.0,
    "overlap": 1.25,
}


@dataclass
class SizingResult:
    lots: float
    risk_pct_used: float
    kelly_fraction: float
    vol_factor: float
    tilt_factor: float
    session_factor: float
    capped: bool = False
    reason: str = ""


class PositionSizer:
    """Calculates dynamic position size via Kelly Criterion with multi-factor scaling."""

    def __init__(
        self,
        config_loader: ConfigLoader | None = None,
        *,
        max_lots: float = 1.0,
        min_lot_step: float = 0.01,
    ) -> None:
        self._max_lots = max_lots
        self._min_lot_step = min_lot_step
        self._vol_factors = dict(_DEFAULT_VOL_FACTORS)
        self._tilt_factors = dict(_DEFAULT_TILT_FACTORS)
        self._session_factors = dict(_DEFAULT_SESSION_FACTORS)
        self._lot_cap_equity_divisor: float = 40_000.0
        self._lot_cap_hard_ceiling: float = 0.50

        if config_loader:
            self._load_config(config_loader)

    def _load_config(self, loader: ConfigLoader) -> None:
        try:
            raw = loader._load_yaml("risk_rules.yaml")
        except FileNotFoundError:
            return

        ps = raw.get("position_sizing", {})
        if vf := ps.get("vol_factor"):
            self._vol_factors.update({str(k): float(v) for k, v in vf.items()})
        if tf := ps.get("tilt_factor"):
            self._tilt_factors.update({str(k): float(v) for k, v in tf.items()})
        if sf := ps.get("session_factor"):
            self._session_factors.update({str(k): float(v) for k, v in sf.items()})

        self._max_lots = raw.get("capital", {}).get(
            "position_size_cap_lots",
            self._max_lots,
        )
        cap_cfg = raw.get("capital", {})
        self._lot_cap_equity_divisor = float(
            cap_cfg.get("lot_cap_equity_divisor", self._lot_cap_equity_divisor)
        )
        self._lot_cap_hard_ceiling = float(
            cap_cfg.get("lot_cap_hard_ceiling", self._lot_cap_hard_ceiling)
        )
        log.info("position_sizer.config_loaded")

    # ── Core Calculation ──────────────────────────────────────────────────

    def calculate_size(
        self,
        balance: float,
        risk_pct: float,
        entry: float,
        sl: float,
        vol_regime: str = "medium",
        tilt_status: str = "normal",
        session: str = "london",
        *,
        symbol: str = "",
        win_rate: float = 0.0,
        avg_win: float = 0.0,
        avg_loss: float = 0.0,
        profit_edge_mult: float = 1.0,
    ) -> SizingResult:
        pip_distance = abs(entry - sl)
        if pip_distance == 0 or balance <= 0:
            return SizingResult(
                lots=0.0, risk_pct_used=0.0, kelly_fraction=0.0,
                vol_factor=0.0, tilt_factor=0.0, session_factor=0.0,
                capped=True, reason="Invalid entry/SL or zero balance",
            )

        vol_f = self.get_vol_factor(vol_regime)
        tilt_f = self.get_tilt_factor(tilt_status)
        sess_f = self.get_session_factor(session)

        if tilt_f == 0.0:
            return SizingResult(
                lots=0.0, risk_pct_used=0.0, kelly_fraction=0.0,
                vol_factor=vol_f, tilt_factor=tilt_f, session_factor=sess_f,
                capped=True, reason="Tilt active — zero size",
            )

        # Kelly cap on risk % when trade memory provides stats
        kelly_frac = 0.0
        effective_risk_pct = risk_pct
        if win_rate > 0 and avg_loss > 0:
            kelly_frac = self.kelly_criterion(win_rate, avg_win, avg_loss)
            if kelly_frac > 0:
                effective_risk_pct = min(risk_pct, kelly_frac * 100.0)

        risk_amount = balance * (effective_risk_pct / 100.0)
        raw_lots = risk_amount / pip_distance

        # Profit-edge multiplier: scale up when recent performance is strong
        edge_mult = max(0.5, min(profit_edge_mult, 1.5))
        adjusted_lots = raw_lots * vol_f * tilt_f * sess_f * edge_mult

        # Asset-aware lot sizing when symbol provided
        if symbol:
            try:
                from shared.asset_specs import get_contract_size, get_pip_size
                contract = get_contract_size(symbol)
                pip_sz = get_pip_size(symbol)
                pips_risked = pip_distance / pip_sz if pip_sz > 0 else 0
                if pips_risked > 0 and contract > 0:
                    adjusted_lots = (risk_amount * edge_mult * vol_f * tilt_f * sess_f) / (
                        pips_risked * pip_sz * contract
                    )
            except Exception:
                pass

        # Round to lot step
        adjusted_lots = round(adjusted_lots / self._min_lot_step) * self._min_lot_step
        adjusted_lots = round(adjusted_lots, 2)

        # ── Hard balance-based lot cap ──────────────────────────────────
        # Never risk more than ~3% of equity in a single position, regardless
        # of what the model/sizer says. A $8,500 account should never trade
        # >0.25 lots on gold (where 1 lot = $1/pip × 100oz). This cap
        # prevented the 1.97-lot automl trade that lost $149 in 0 minutes.
        # Both values come from risk_rules.yaml → capital section.
        _balance_max_lots = max(
            self._min_lot_step,
            round(balance / self._lot_cap_equity_divisor, 2),
        )
        _HARD_CEILING_LOTS = self._lot_cap_hard_ceiling

        # Effective max lots. A configured cap of 0 (or less) means "no manual
        # cap — size dynamically from equity" (NOT a hard 0-lot cap, which would
        # zero out every order and make MT5 reject them as 'Invalid volume').
        if self._max_lots and self._max_lots > 0:
            effective_max = min(self._max_lots, _balance_max_lots, _HARD_CEILING_LOTS)
        else:
            effective_max = min(_balance_max_lots, _HARD_CEILING_LOTS)

        capped = False
        reason = ""
        if adjusted_lots > effective_max:
            adjusted_lots = effective_max
            capped = True
            reason = f"Capped at max {effective_max} lots"
        # Floor a valid (positive) signal at the broker minimum lot instead of
        # dropping it to 0 — a sub-minimum computed size still represents a real
        # entry the system wants to take.
        if 0 < adjusted_lots < self._min_lot_step:
            adjusted_lots = self._min_lot_step
            capped = True
            reason = "Raised to minimum lot step"

        actual_risk_pct = (adjusted_lots * pip_distance / balance) * 100 if balance > 0 else 0.0

        log.info(
            "position_sizer.calculated",
            lots=adjusted_lots,
            risk_pct=round(actual_risk_pct, 2),
            vol_factor=vol_f,
            tilt_factor=tilt_f,
            session_factor=sess_f,
        )

        return SizingResult(
            lots=adjusted_lots,
            risk_pct_used=round(actual_risk_pct, 4),
            kelly_fraction=kelly_frac,
            vol_factor=vol_f,
            tilt_factor=tilt_f,
            session_factor=sess_f,
            capped=capped,
            reason=reason,
        )

    def max_allowed_lots(self, balance: float) -> float:
        """Absolute lot ceiling for this balance (manual cap ∧ equity cap ∧ hard ceiling).

        Exposed so callers that scale the sized lots afterwards (e.g. conviction
        ×1.15) can re-clamp — otherwise post-cap multipliers leak above the cap.
        """
        balance_max = max(
            self._min_lot_step,
            round(max(balance, 0.0) / self._lot_cap_equity_divisor, 2),
        )
        if self._max_lots and self._max_lots > 0:
            return min(self._max_lots, balance_max, self._lot_cap_hard_ceiling)
        return min(balance_max, self._lot_cap_hard_ceiling)

    # ── Kelly Criterion ───────────────────────────────────────────────────

    @staticmethod
    def kelly_criterion(win_rate: float, avg_win: float, avg_loss: float) -> float:
        """
        Kelly fraction: f* = (p × b − q) / b
        where p = win_rate, q = 1 − p, b = avg_win / |avg_loss|
        """
        if avg_loss == 0 or win_rate <= 0 or win_rate >= 1:
            return 0.0

        b = abs(avg_win / avg_loss)
        q = 1.0 - win_rate
        kelly = (win_rate * b - q) / b

        kelly = max(0.0, min(kelly, 0.25))
        return round(kelly, 4)

    # ── Conviction Scaling ────────────────────────────────────────────────

    @staticmethod
    def scale_by_conviction(base_size: float, conviction: float) -> float:
        """Scale position size linearly by conviction (0.0–1.0)."""
        conviction = max(0.0, min(1.0, conviction))
        if conviction < 0.5:
            scale = 0.5
        elif conviction < 0.7:
            scale = 0.75
        elif conviction < 0.85:
            scale = 1.0
        else:
            scale = 1.15
        return round(base_size * scale, 2)

    # ── Factor Lookups ────────────────────────────────────────────────────

    def get_vol_factor(self, regime: str) -> float:
        return self._vol_factors.get(regime.lower(), 0.75)

    def get_tilt_factor(self, status: str) -> float:
        return self._tilt_factors.get(status.lower(), 1.0)

    def get_session_factor(self, session: str) -> float:
        return self._session_factors.get(session.lower(), 0.5)
