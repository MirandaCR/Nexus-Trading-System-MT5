"""
Enhanced backtester with SL/TP support.

Unlike SimpleBacktester (max_hold_bars exit only), this one simulates
realistic stop-loss and take-profit execution, partial closes, and
trailing stops — matching the live risk pipeline behaviour.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SLTPBacktestResult:
    """Performance metrics from a SL/TP-aware backtest."""

    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    avg_win_pips: float = 0.0
    avg_loss_pips: float = 0.0
    avg_rr: float = 0.0
    expectancy: float = 0.0
    avg_hold_bars: float = 0.0
    sl_hit_pct: float = 0.0
    tp_hit_pct: float = 0.0
    time_exit_pct: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    trade_log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_return": round(self.total_return, 6),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "sortino_ratio": round(self.sortino_ratio, 4),
            "max_drawdown": round(self.max_drawdown, 6),
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "total_trades": self.total_trades,
            "avg_win_pips": round(self.avg_win_pips, 1),
            "avg_loss_pips": round(self.avg_loss_pips, 1),
            "avg_rr": round(self.avg_rr, 2),
            "expectancy": round(self.expectancy, 6),
            "avg_hold_bars": round(self.avg_hold_bars, 1),
            "sl_hit_pct": round(self.sl_hit_pct, 2),
            "tp_hit_pct": round(self.tp_hit_pct, 2),
            "time_exit_pct": round(self.time_exit_pct, 2),
        }


@dataclass
class _Position:
    entry_idx: int
    entry_price: float
    direction: int        # 1=long, -1=short
    sl_price: float
    tp_price: float
    size: float = 1.0
    trailing_active: bool = False
    trailing_sl: float = 0.0


class SLTPBacktester:
    """Bar-by-bar backtester with stop-loss/take-profit simulation."""

    def __init__(
        self,
        spread_pips: float = 1.5,
        pip_value: float = 0.0001,
        initial_capital: float = 10_000.0,
        risk_per_trade: float = 0.01,
        max_hold_bars: int = 15,       # scalping default: 15 bars
        trailing_trigger_rr: float = 1.2,
        trailing_offset_pips: float = 5.0,
        bars_per_year: int = 252 * 24 * 12,  # M5 bars per year
    ) -> None:
        self.spread_pips = spread_pips
        self.pip_value = pip_value
        self.initial_capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.max_hold_bars = max_hold_bars
        self.trailing_trigger_rr = trailing_trigger_rr
        self.trailing_offset_pips = trailing_offset_pips
        self.bars_per_year = bars_per_year

    def backtest(
        self,
        signals: np.ndarray,
        sl_pips: np.ndarray,
        tp_pips: np.ndarray,
        ohlcv: np.ndarray,
        confidences: np.ndarray | None = None,
    ) -> SLTPBacktestResult:
        """Run SL/TP-aware backtest.

        Parameters
        ----------
        signals     : (N,) int — 1=BUY, -1=SELL, 0=NO_TRADE
        sl_pips     : (N,) float — stop-loss distance per bar
        tp_pips     : (N,) float — take-profit distance per bar
        ohlcv       : (N, 5) — open, high, low, close, volume
        confidences : optional (N,) float for position sizing
        """
        n = len(signals)
        assert ohlcv.shape == (n, 5)

        if confidences is None:
            confidences = np.ones(n)

        capital = self.initial_capital
        equity_curve = [capital]
        trade_log: list[dict[str, Any]] = []
        position: _Position | None = None

        exit_reasons = {"sl": 0, "tp": 0, "time": 0, "signal": 0}

        for i in range(n):
            o, h, l, c, v = ohlcv[i]

            # ── Check open position ──
            if position is not None:
                bars_held = i - position.entry_idx
                exit_price = None
                exit_reason = ""

                # Check SL hit
                if position.direction == 1:  # long
                    active_sl = position.trailing_sl if position.trailing_active else position.sl_price
                    if l <= active_sl:
                        exit_price = active_sl
                        exit_reason = "sl"
                    elif h >= position.tp_price:
                        exit_price = position.tp_price
                        exit_reason = "tp"
                else:  # short
                    active_sl = position.trailing_sl if position.trailing_active else position.sl_price
                    if h >= active_sl:
                        exit_price = active_sl
                        exit_reason = "sl"
                    elif l <= position.tp_price:
                        exit_price = position.tp_price
                        exit_reason = "tp"

                # Time exit
                if exit_price is None and bars_held >= self.max_hold_bars:
                    exit_price = c
                    exit_reason = "time"

                # Signal reversal
                if exit_price is None and signals[i] == -position.direction:
                    exit_price = c
                    exit_reason = "signal"

                if exit_price is not None:
                    pnl_raw = (exit_price - position.entry_price) * position.direction
                    pnl_pips = pnl_raw / self.pip_value
                    spread_cost = self.spread_pips * self.pip_value * position.size
                    net_pnl = pnl_raw * position.size - spread_cost

                    sl_dist = abs(position.entry_price - position.sl_price) / self.pip_value
                    rr = abs(pnl_pips / sl_dist) if sl_dist > 0 else 0.0

                    capital += net_pnl
                    trade_log.append({
                        "entry_idx": position.entry_idx,
                        "exit_idx": i,
                        "direction": "BUY" if position.direction == 1 else "SELL",
                        "entry_price": position.entry_price,
                        "exit_price": exit_price,
                        "sl_price": position.sl_price,
                        "tp_price": position.tp_price,
                        "pnl_pips": pnl_pips,
                        "pnl_usd": net_pnl,
                        "rr": rr if pnl_pips > 0 else -rr,
                        "bars_held": bars_held,
                        "exit_reason": exit_reason,
                    })
                    exit_reasons[exit_reason] = exit_reasons.get(exit_reason, 0) + 1
                    position = None
                else:
                    # Trailing stop logic
                    if not position.trailing_active:
                        pnl_pips_current = (c - position.entry_price) * position.direction / self.pip_value
                        sl_dist = abs(position.entry_price - position.sl_price) / self.pip_value
                        if sl_dist > 0 and pnl_pips_current / sl_dist >= self.trailing_trigger_rr:
                            position.trailing_active = True
                            offset = self.trailing_offset_pips * self.pip_value
                            if position.direction == 1:
                                position.trailing_sl = c - offset
                            else:
                                position.trailing_sl = c + offset
                    elif position.trailing_active:
                        offset = self.trailing_offset_pips * self.pip_value
                        if position.direction == 1:
                            new_trail = c - offset
                            if new_trail > position.trailing_sl:
                                position.trailing_sl = new_trail
                        else:
                            new_trail = c + offset
                            if new_trail < position.trailing_sl:
                                position.trailing_sl = new_trail

            # ── Check entry ──
            if position is None and signals[i] != 0 and sl_pips[i] > 0 and tp_pips[i] > 0:
                direction = int(signals[i])
                sl_dist = sl_pips[i] * self.pip_value
                tp_dist = tp_pips[i] * self.pip_value

                if direction == 1:
                    sl_price = c - sl_dist
                    tp_price = c + tp_dist
                else:
                    sl_price = c + sl_dist
                    tp_price = c - tp_dist

                size = self._position_size(capital, sl_pips[i], confidences[i])

                position = _Position(
                    entry_idx=i,
                    entry_price=c,
                    direction=direction,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    size=size,
                )

            equity_curve.append(capital)

        # Close remaining position at market
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
                "rr": 0.0,
                "bars_held": n - 1 - position.entry_idx,
                "exit_reason": "end",
            })
            equity_curve.append(capital)

        return self._compute_metrics(equity_curve, trade_log, exit_reasons)

    def _position_size(self, capital: float, sl_pips: float, confidence: float) -> float:
        risk_amount = capital * self.risk_per_trade * min(1.0, confidence)
        pip_value_per_lot = sl_pips * self.pip_value * 100_000
        if pip_value_per_lot <= 0:
            return 0.01
        lots = risk_amount / pip_value_per_lot
        return max(0.01, min(lots, 5.0))

    def _compute_metrics(
        self,
        equity_curve: list[float],
        trade_log: list[dict[str, Any]],
        exit_reasons: dict[str, int],
    ) -> SLTPBacktestResult:
        eq = np.array(equity_curve)
        total = len(trade_log)

        if len(eq) < 2 or total == 0:
            return SLTPBacktestResult(equity_curve=equity_curve, trade_log=trade_log)

        returns = np.diff(eq) / np.maximum(eq[:-1], 1e-10)
        returns = returns[np.isfinite(returns)]

        total_return = (eq[-1] - eq[0]) / eq[0] if eq[0] != 0 else 0.0
        mean_ret = float(np.mean(returns)) if len(returns) > 0 else 0.0
        std_ret = float(np.std(returns)) if len(returns) > 1 else 1.0
        sharpe = (mean_ret / std_ret * np.sqrt(self.bars_per_year)) if std_ret > 0 else 0.0

        downside = returns[returns < 0]
        down_std = float(np.std(downside)) if len(downside) > 1 else 1.0
        sortino = (mean_ret / down_std * np.sqrt(self.bars_per_year)) if down_std > 0 else 0.0

        running_max = np.maximum.accumulate(eq)
        drawdowns = (eq - running_max) / np.maximum(running_max, 1e-10)
        max_dd = float(np.min(drawdowns))

        pnls = [t["pnl_pips"] for t in trade_log]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        win_rate = len(wins) / total if total > 0 else 0.0
        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 1.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

        rr_values = [abs(t.get("rr", 0)) for t in trade_log if t.get("rr", 0) != 0]
        avg_rr = float(np.mean(rr_values)) if rr_values else 0.0

        bars_held = [t["bars_held"] for t in trade_log]
        avg_hold = float(np.mean(bars_held)) if bars_held else 0.0

        expectancy = (win_rate * avg_win + (1 - win_rate) * avg_loss) if total > 0 else 0.0

        sl_pct = exit_reasons.get("sl", 0) / total * 100 if total > 0 else 0.0
        tp_pct = exit_reasons.get("tp", 0) / total * 100 if total > 0 else 0.0
        time_pct = exit_reasons.get("time", 0) / total * 100 if total > 0 else 0.0

        return SLTPBacktestResult(
            total_return=total_return,
            sharpe_ratio=float(sharpe),
            sortino_ratio=float(sortino),
            max_drawdown=max_dd,
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_trades=total,
            avg_win_pips=avg_win,
            avg_loss_pips=avg_loss,
            avg_rr=avg_rr,
            expectancy=expectancy,
            avg_hold_bars=avg_hold,
            sl_hit_pct=sl_pct,
            tp_hit_pct=tp_pct,
            time_exit_pct=time_pct,
            equity_curve=equity_curve,
            trade_log=trade_log,
        )
