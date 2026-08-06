"""
Nexus Risk — Real-time correlation analysis for portfolio risk.

Calculates rolling Pearson correlation between symbol pairs and validates
that a new position does not introduce excessive portfolio correlation.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import structlog

log = structlog.get_logger(__name__)

# ── Static correlation estimates (fallback when no live data available) ───────

_STATIC_CORRELATIONS: dict[tuple[str, str], float] = {
    ("EURUSD", "GBPUSD"):  0.85,
    ("EURUSD", "USDCHF"): -0.90,
    ("EURUSD", "EURJPY"):  0.60,
    ("EURUSD", "EURGBP"):  0.45,
    ("GBPUSD", "EURGBP"): -0.70,
    ("GBPUSD", "GBPJPY"):  0.75,
    ("AUDUSD", "NZDUSD"):  0.90,
    ("AUDUSD", "AUDJPY"):  0.65,
    ("USDJPY", "EURJPY"):  0.80,
    ("USDCAD", "USDCHF"):  0.55,
    ("XAUUSD", "EURUSD"):  0.40,
    ("XAUUSD", "USDJPY"): -0.35,
}


def _normalise_sym(s: str) -> str:
    return s.upper().replace("/", "").replace(".", "")


class CorrelationChecker:
    """Real-time correlation checker with cached price series."""

    def __init__(
        self,
        max_correlation: float = 0.7,
        default_period: int = 100,
    ) -> None:
        self._max_correlation = max_correlation
        self._default_period = default_period
        self._price_cache: dict[str, list[float]] = {}

    def update_price(self, symbol: str, price: float) -> None:
        sym = _normalise_sym(symbol)
        if sym not in self._price_cache:
            self._price_cache[sym] = []
        self._price_cache[sym].append(price)
        if len(self._price_cache[sym]) > self._default_period * 2:
            self._price_cache[sym] = self._price_cache[sym][-self._default_period * 2:]

    # ── Core Correlation ──────────────────────────────────────────────────

    def calculate_correlation(
        self,
        symbol_a: str,
        symbol_b: str,
        period: int | None = None,
    ) -> float:
        period = period or self._default_period
        sym_a = _normalise_sym(symbol_a)
        sym_b = _normalise_sym(symbol_b)

        if sym_a == sym_b:
            return 1.0

        prices_a = self._price_cache.get(sym_a, [])
        prices_b = self._price_cache.get(sym_b, [])

        min_len = min(len(prices_a), len(prices_b))
        if min_len < 20:
            return self._static_fallback(sym_a, sym_b)

        n = min(min_len, period)
        arr_a = np.array(prices_a[-n:], dtype=np.float64)
        arr_b = np.array(prices_b[-n:], dtype=np.float64)

        ret_a = np.diff(arr_a) / arr_a[:-1]
        ret_b = np.diff(arr_b) / arr_b[:-1]

        if len(ret_a) < 10 or np.std(ret_a) == 0 or np.std(ret_b) == 0:
            return self._static_fallback(sym_a, sym_b)

        corr = float(np.corrcoef(ret_a, ret_b)[0, 1])
        if np.isnan(corr):
            return self._static_fallback(sym_a, sym_b)
        return round(corr, 4)

    def _static_fallback(self, sym_a: str, sym_b: str) -> float:
        val = _STATIC_CORRELATIONS.get((sym_a, sym_b))
        if val is not None:
            return val
        val = _STATIC_CORRELATIONS.get((sym_b, sym_a))
        if val is not None:
            return val
        return 0.0

    # ── Portfolio Validation ──────────────────────────────────────────────

    def check_portfolio_correlation(
        self,
        new_symbol: str,
        open_positions: list[dict[str, Any]],
    ) -> tuple[bool, list[dict[str, Any]]]:
        new_sym = _normalise_sym(new_symbol)
        details: list[dict[str, Any]] = []
        ok = True

        for pos in open_positions:
            pos_sym = _normalise_sym(pos.get("symbol", ""))
            if pos_sym == new_sym:
                details.append({
                    "pair": f"{new_sym}/{pos_sym}",
                    "correlation": 1.0,
                    "status": "DUPLICATE",
                })
                ok = False
                continue

            corr = self.calculate_correlation(new_sym, pos_sym)
            status = "OK" if abs(corr) <= self._max_correlation else "EXCEEDS_LIMIT"
            if abs(corr) > self._max_correlation:
                ok = False

            details.append({
                "pair": f"{new_sym}/{pos_sym}",
                "correlation": round(corr, 4),
                "status": status,
                "limit": self._max_correlation,
            })

        log.info(
            "correlation.portfolio_check",
            symbol=new_sym,
            positions=len(open_positions),
            ok=ok,
        )
        return ok, details

    # ── Correlation Matrix ────────────────────────────────────────────────

    def get_correlation_matrix(self, symbols: list[str]) -> pd.DataFrame:
        normed = [_normalise_sym(s) for s in symbols]
        n = len(normed)
        matrix = np.eye(n)

        for i in range(n):
            for j in range(i + 1, n):
                corr = self.calculate_correlation(normed[i], normed[j])
                matrix[i, j] = corr
                matrix[j, i] = corr

        return pd.DataFrame(matrix, index=normed, columns=normed)
