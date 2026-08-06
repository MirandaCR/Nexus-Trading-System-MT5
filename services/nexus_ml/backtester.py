"""
Simple signal-based backtester.

Takes a stream of signals and OHLCV data and computes performance metrics.
Designed to be lightweight for quick feedback; hooks are provided for
vectorbt integration when deeper analysis is needed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Aggregated performance from a backtest run."""

    total_return: float = 0.0
    annualised_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    trade_log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_return": round(self.total_return, 6),
            "annualised_return": round(self.annualised_return, 6),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "sortino_ratio": round(self.sortino_ratio, 4),
            "max_drawdown": round(self.max_drawdown, 6),
            "max_drawdown_duration": self.max_drawdown_duration,
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "total_trades": self.total_trades,
            "avg_win": round(self.avg_win, 6),
            "avg_loss": round(self.avg_loss, 6),
            "expectancy": round(self.expectancy, 6),
        }


@dataclass
class _OpenPosition:
    entry_idx: int
    entry_price: float
    direction: int  # 1 = long, -1 = short
    size: float = 1.0


class SimpleBacktester:
    """Event-driven backtester operating on aligned signal + OHLCV arrays."""

    def __init__(
        self,
        spread_pips: float = 1.5,
        pip_value: float = 0.0001,
        initial_capital: float = 10_000.0,
        risk_per_trade: float = 0.02,
        bars_per_year: int = 252 * 24,
        max_hold_bars: int = 60,
    ) -> None:
        self.spread_pips = spread_pips
        self.pip_value = pip_value
        self.initial_capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.bars_per_year = bars_per_year
        self.max_hold_bars = max_hold_bars

    # ── Main entry ────────────────────────────────────────────────────────

    def backtest(
        self,
        signals: np.ndarray,
        ohlcv: np.ndarray,
        confidences: np.ndarray | None = None,
    ) -> BacktestResult:
        """Run the backtest.

        Parameters
        ----------
        signals   : (N,) int — 1=BUY, -1=SELL, 0=NO_TRADE.
        ohlcv     : (N, 5) — open, high, low, close, volume.
        confidences : optional (N,) float ∈ [0,1] used for position sizing.
        """
        n = len(signals)
        assert ohlcv.shape == (n, 5), "ohlcv must be (N, 5)"

        if confidences is None:
            confidences = np.ones(n)

        capital = self.initial_capital
        equity_curve = [capital]
        trade_log: list[dict[str, Any]] = []
        position: _OpenPosition | None = None

        for i in range(n):
            o, h, l, c, v = ohlcv[i]

            # Check exit conditions for open position
            if position is not None:
                bars_held = i - position.entry_idx
                pnl_raw = (c - position.entry_price) * position.direction
                pnl_pips = pnl_raw / self.pip_value

                should_exit = (
                    bars_held >= self.max_hold_bars
                    or signals[i] == -position.direction
                    or signals[i] == 0
                )

                if should_exit:
                    spread_cost = self.spread_pips * self.pip_value * position.size
                    net_pnl = pnl_raw * position.size - spread_cost
                    capital += net_pnl

                    trade_log.append({
                        "entry_idx": position.entry_idx,
                        "exit_idx": i,
                        "direction": "BUY" if position.direction == 1 else "SELL",
                        "entry_price": position.entry_price,
                        "exit_price": c,
                        "pnl_pips": pnl_pips,
                        "pnl_usd": net_pnl,
                        "bars_held": bars_held,
                    })
                    position = None

            # Check entry conditions
            if position is None and signals[i] != 0:
                size = self._position_size(capital, confidences[i])
                position = _OpenPosition(
                    entry_idx=i,
                    entry_price=c,
                    direction=int(signals[i]),
                    size=size,
                )

            equity_curve.append(capital)

        # Close any remaining position at last close
        if position is not None:
            c = ohlcv[-1, 3]
            pnl_raw = (c - position.entry_price) * position.direction
            spread_cost = self.spread_pips * self.pip_value * position.size
            capital += pnl_raw * position.size - spread_cost
            trade_log.append({
                "entry_idx": position.entry_idx,
                "exit_idx": n - 1,
                "direction": "BUY" if position.direction == 1 else "SELL",
                "entry_price": position.entry_price,
                "exit_price": c,
                "pnl_pips": pnl_raw / self.pip_value,
                "pnl_usd": pnl_raw * position.size - spread_cost,
                "bars_held": n - 1 - position.entry_idx,
            })
            equity_curve.append(capital)

        return self._compute_metrics(equity_curve, trade_log)

    # ── Metrics calculation ───────────────────────────────────────────────

    def _compute_metrics(
        self,
        equity_curve: list[float],
        trade_log: list[dict[str, Any]],
    ) -> BacktestResult:
        eq = np.array(equity_curve)

        if len(eq) < 2:
            return BacktestResult(equity_curve=equity_curve, trade_log=trade_log)

        returns = np.diff(eq) / eq[:-1]
        returns = returns[np.isfinite(returns)]

        total_return = (eq[-1] - eq[0]) / eq[0] if eq[0] != 0 else 0.0
        n_periods = len(returns)
        ann_factor = self.bars_per_year / max(n_periods, 1)
        annualised_return = (1 + total_return) ** ann_factor - 1 if n_periods > 0 else 0.0

        mean_ret = float(np.mean(returns)) if len(returns) > 0 else 0.0
        std_ret = float(np.std(returns)) if len(returns) > 1 else 1.0
        sharpe = (mean_ret / std_ret * np.sqrt(self.bars_per_year)) if std_ret > 0 else 0.0

        downside = returns[returns < 0]
        down_std = float(np.std(downside)) if len(downside) > 1 else 1.0
        sortino = (mean_ret / down_std * np.sqrt(self.bars_per_year)) if down_std > 0 else 0.0

        running_max = np.maximum.accumulate(eq)
        drawdowns = (eq - running_max) / running_max
        max_dd = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0

        dd_duration = 0
        max_dd_dur = 0
        for dd in drawdowns:
            if dd < 0:
                dd_duration += 1
                max_dd_dur = max(max_dd_dur, dd_duration)
            else:
                dd_duration = 0

        pnls = [t["pnl_usd"] for t in trade_log]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_trades = len(trade_log)
        win_rate = len(wins) / total_trades if total_trades > 0 else 0.0
        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 1.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
        expectancy = (win_rate * avg_win + (1 - win_rate) * avg_loss) if total_trades > 0 else 0.0

        return BacktestResult(
            total_return=total_return,
            annualised_return=annualised_return,
            sharpe_ratio=float(sharpe),
            sortino_ratio=float(sortino),
            max_drawdown=max_dd,
            max_drawdown_duration=max_dd_dur,
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_trades=total_trades,
            avg_win=avg_win,
            avg_loss=avg_loss,
            expectancy=expectancy,
            equity_curve=equity_curve,
            trade_log=trade_log,
        )

    # ── Position sizing ───────────────────────────────────────────────────

    def _position_size(self, capital: float, confidence: float) -> float:
        risk_amount = capital * self.risk_per_trade * confidence
        return max(0.01, risk_amount / (self.spread_pips * self.pip_value * 100))

    # ── vectorbt integration hook ─────────────────────────────────────────

    @staticmethod
    def to_vectorbt(
        signals: np.ndarray,
        close_prices: np.ndarray,
    ) -> Any:
        """Convert signals + prices to a vectorbt Portfolio.

        Requires vectorbt to be installed.
        """
        import vectorbt as vbt

        entries = signals == 1
        exits = signals == -1

        return vbt.Portfolio.from_signals(
            close=close_prices,
            entries=entries,
            exits=exits,
            init_cash=10_000,
            fees=0.0002,
            slippage=0.0001,
        )
