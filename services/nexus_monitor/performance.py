"""
Nexus Monitor — Performance analytics engine.

Calculates Sharpe ratio, Sortino ratio, max drawdown, profit factor,
equity curves, and daily P&L using numpy/pandas.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import structlog

from shared.models import PerformanceMetrics

log = structlog.get_logger(__name__)

_ANNUALISATION_FACTOR = 252  # trading days per year


@dataclass
class DetailedMetrics:
    total_pnl: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_rr: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    avg_duration_min: float = 0.0
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    expectancy: float = 0.0


class PerformanceAnalyzer:
    """Computes comprehensive trading performance metrics."""

    def __init__(self, risk_free_rate: float = 0.0) -> None:
        self._risk_free = risk_free_rate

    # ── Master Calculation ────────────────────────────────────────────────

    def calculate_metrics(self, trades: list[dict[str, Any]]) -> DetailedMetrics:
        if not trades:
            return DetailedMetrics()

        pnls = [float(t.get("pnl_usd", 0)) for t in trades]
        pnl_arr = np.array(pnls, dtype=np.float64)

        wins = pnl_arr[pnl_arr > 0]
        losses = pnl_arr[pnl_arr <= 0]

        total_pnl = float(np.sum(pnl_arr))
        total_trades = len(pnls)
        winning = len(wins)
        losing = len(losses)
        win_rate = (winning / total_trades * 100) if total_trades > 0 else 0
        avg_win = float(np.mean(wins)) if len(wins) > 0 else 0
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0

        equity = self.equity_curve(trades)
        eq_values = equity.values if isinstance(equity, pd.Series) else np.cumsum(pnl_arr)
        max_dd = self.calculate_max_drawdown(eq_values)

        rrs = [float(t.get("rr_ratio", 0)) for t in trades]
        durations = [float(t.get("duration", 0)) for t in trades]

        daily = self.daily_pnl(trades)
        daily_returns = daily.values if isinstance(daily, pd.Series) else pnl_arr
        sharpe = self.calculate_sharpe(daily_returns, self._risk_free)
        sortino = self.calculate_sortino(daily_returns)

        profit_factor = self.calculate_profit_factor(trades)
        c_wins, c_losses = self._max_consecutive(pnls)

        expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss) if total_trades > 0 else 0

        return DetailedMetrics(
            total_pnl=round(total_pnl, 2),
            win_rate=round(win_rate, 2),
            profit_factor=round(profit_factor, 2),
            sharpe_ratio=round(sharpe, 4),
            sortino_ratio=round(sortino, 4),
            max_drawdown=round(max_dd, 2),
            max_drawdown_pct=round(max_dd, 2),
            total_trades=total_trades,
            winning_trades=winning,
            losing_trades=losing,
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            avg_rr=round(float(np.mean(rrs)), 2) if rrs else 0,
            best_trade=round(float(np.max(pnl_arr)), 2),
            worst_trade=round(float(np.min(pnl_arr)), 2),
            avg_duration_min=round(float(np.mean(durations)), 1) if durations else 0,
            consecutive_wins=c_wins,
            consecutive_losses=c_losses,
            expectancy=round(expectancy, 2),
        )

    def to_shared_model(self, trades: list[dict[str, Any]]) -> PerformanceMetrics:
        m = self.calculate_metrics(trades)
        return PerformanceMetrics(
            total_pnl=m.total_pnl,
            win_rate=m.win_rate,
            profit_factor=m.profit_factor,
            sharpe_ratio=m.sharpe_ratio,
            max_drawdown=m.max_drawdown,
            total_trades=m.total_trades,
            avg_win=m.avg_win,
            avg_loss=m.avg_loss,
        )

    # ── Individual Metric Calculations ────────────────────────────────────

    @staticmethod
    def calculate_sharpe(returns: np.ndarray | pd.Series, risk_free: float = 0.0) -> float:
        arr = np.asarray(returns, dtype=np.float64)
        if len(arr) < 2:
            return 0.0
        excess = arr - risk_free / _ANNUALISATION_FACTOR
        std = np.std(excess, ddof=1)
        if std == 0:
            return 0.0
        return float(np.mean(excess) / std * np.sqrt(_ANNUALISATION_FACTOR))

    @staticmethod
    def calculate_sortino(returns: np.ndarray | pd.Series, risk_free: float = 0.0) -> float:
        arr = np.asarray(returns, dtype=np.float64)
        if len(arr) < 2:
            return 0.0
        excess = arr - risk_free / _ANNUALISATION_FACTOR
        downside = excess[excess < 0]
        if len(downside) == 0:
            return float(np.mean(excess) * np.sqrt(_ANNUALISATION_FACTOR)) if np.mean(excess) > 0 else 0.0
        downside_std = np.std(downside, ddof=1)
        if downside_std == 0:
            return 0.0
        return float(np.mean(excess) / downside_std * np.sqrt(_ANNUALISATION_FACTOR))

    @staticmethod
    def calculate_max_drawdown(equity_curve: np.ndarray | pd.Series) -> float:
        arr = np.asarray(equity_curve, dtype=np.float64)
        if len(arr) < 2:
            return 0.0
        peak = np.maximum.accumulate(arr)
        drawdown = peak - arr
        max_dd = float(np.max(drawdown))
        if peak[np.argmax(drawdown)] > 0:
            return max_dd / peak[np.argmax(drawdown)] * 100
        return max_dd

    @staticmethod
    def calculate_profit_factor(trades: list[dict[str, Any]]) -> float:
        gross_profit = sum(t.get("pnl_usd", 0) for t in trades if t.get("pnl_usd", 0) > 0)
        gross_loss = abs(sum(t.get("pnl_usd", 0) for t in trades if t.get("pnl_usd", 0) < 0))
        if gross_loss == 0:
            return float(gross_profit) if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    @staticmethod
    def calculate_win_rate(trades: list[dict[str, Any]]) -> float:
        if not trades:
            return 0.0
        wins = sum(1 for t in trades if t.get("pnl_usd", 0) > 0)
        return wins / len(trades) * 100

    # ── Time Series ───────────────────────────────────────────────────────

    @staticmethod
    def daily_pnl(trades: list[dict[str, Any]]) -> pd.Series:
        if not trades:
            return pd.Series(dtype=float)

        records = []
        for t in trades:
            closed_at = t.get("closed_at")
            pnl = t.get("pnl_usd", 0)
            if closed_at is not None:
                records.append({"date": pd.to_datetime(closed_at), "pnl": pnl})

        if not records:
            return pd.Series([t.get("pnl_usd", 0) for t in trades], dtype=float)

        df = pd.DataFrame(records)
        df["date"] = df["date"].dt.date
        daily = df.groupby("date")["pnl"].sum()
        return daily

    @staticmethod
    def equity_curve(trades: list[dict[str, Any]]) -> pd.Series:
        pnls = [float(t.get("pnl_usd", 0)) for t in trades]
        cumulative = np.cumsum(pnls)
        return pd.Series(cumulative, dtype=float)

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _max_consecutive(pnls: list[float]) -> tuple[int, int]:
        max_wins = 0
        max_losses = 0
        cur_wins = 0
        cur_losses = 0

        for pnl in pnls:
            if pnl > 0:
                cur_wins += 1
                cur_losses = 0
                max_wins = max(max_wins, cur_wins)
            else:
                cur_losses += 1
                cur_wins = 0
                max_losses = max(max_losses, cur_losses)

        return max_wins, max_losses
