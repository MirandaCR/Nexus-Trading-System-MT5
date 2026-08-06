"""
Base class for all parallel model traders.

Defines the common interface (train / predict / evaluate) and shared
data structures (TradeSignal, ModelPerformance) that the API layer
and dashboard consume.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ── Data Structures ─────────────────────────────────────────────────────────


@dataclass
class TradeSignal:
    """Signal produced by any model trader."""

    model_type: str          # "ml" | "dl" | "rl"
    model_name: str          # e.g. "XGBoost_SL_TP", "Transformer_v1", "PPO_v1"
    symbol: str
    direction: str           # "BUY" | "SELL" | "NO_TRADE"
    confidence: float        # 0..1
    sl_pips: float = 0.0     # suggested stop-loss in pips
    tp_pips: float = 0.0     # suggested take-profit in pips
    lot_size: float = 0.0    # dynamic lot size (0 = use default)
    regime: str = "MEDIUM"
    timeframe: str = "M15"
    session: str = ""        # current trading session
    features_used: int = 0
    mtf_confirmed: bool = True   # multi-timeframe confirmation
    spread_ok: bool = True       # spread pre-filter passed
    reasoning: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_type": self.model_type,
            "model_name": self.model_name,
            "symbol": self.symbol,
            "direction": self.direction,
            "confidence": round(self.confidence, 4),
            "sl_pips": round(self.sl_pips, 1),
            "tp_pips": round(self.tp_pips, 1),
            "lot_size": round(self.lot_size, 2),
            "regime": self.regime,
            "timeframe": self.timeframe,
            "session": self.session,
            "features_used": self.features_used,
            "mtf_confirmed": self.mtf_confirmed,
            "spread_ok": self.spread_ok,
            "reasoning": self.reasoning,
            "timestamp": self.timestamp,
        }


@dataclass
class ModelPerformance:
    """Tracks backtest/live performance for leaderboard comparison."""

    model_type: str
    model_name: str
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    total_return: float = 0.0
    avg_rr: float = 0.0
    avg_win_pips: float = 0.0
    avg_loss_pips: float = 0.0
    expectancy: float = 0.0
    regime: str = "ALL"
    timeframe: str = "M15"
    train_samples: int = 0
    last_trained: str = ""
    is_fitted: bool = False
    equity_curve: list[float] = field(default_factory=list)
    recent_signals: list[dict[str, Any]] = field(default_factory=list)
    training_time_sec: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_type": self.model_type,
            "model_name": self.model_name,
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "sortino_ratio": round(self.sortino_ratio, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "total_return": round(self.total_return, 4),
            "avg_rr": round(self.avg_rr, 2),
            "avg_win_pips": round(self.avg_win_pips, 1),
            "avg_loss_pips": round(self.avg_loss_pips, 1),
            "expectancy": round(self.expectancy, 4),
            "regime": self.regime,
            "timeframe": self.timeframe,
            "train_samples": self.train_samples,
            "last_trained": self.last_trained,
            "is_fitted": self.is_fitted,
            "equity_curve": self.equity_curve[-200:],  # last 200 points
            "recent_signals": self.recent_signals[-20:],
            "training_time_sec": round(self.training_time_sec, 2),
        }


# ── Pip Value Utility ─────────────────────────────────────────────────────


def get_pip_value(symbol: str) -> float:
    """Return pip size in price units — delegates to shared.asset_specs."""
    from shared.asset_specs import get_pip_size
    return get_pip_size(symbol)


# ── Multi-Timeframe Feature Builder ────────────────────────────────────────


def build_mtf_features(
    features_by_tf: dict[str, dict[str, float]],
    operational_tf: str = "M15",
) -> dict[str, float]:
    """Flatten multi-timeframe features into a single feature vector.

    Input: {"M15": {rsi: 55, ema_20: 1.10, ...}, "H1": {...}, "H4": {...}}
    Output: {"M15_rsi": 55, "M15_ema_20": 1.10, "H1_rsi": 60, ...}
    """
    flat: dict[str, float] = {}
    for tf, feats in features_by_tf.items():
        for k, v in feats.items():
            try:
                flat[f"{tf}_{k}"] = float(v)
            except (ValueError, TypeError):
                continue
    return flat


def compute_sl_tp_features(
    features: dict[str, float],
    prefix: str = "",
) -> dict[str, float]:
    """Add SL/TP-relevant features derived from volatility indicators."""
    out: dict[str, float] = {}
    atr = features.get(f"{prefix}atr", features.get("atr", 0.0))
    close = features.get(f"{prefix}close", features.get("close", 0.0))

    if atr > 0:
        out["atr_pct"] = (atr / close * 100) if close > 0 else 0.0
        out["sl_atr_1x"] = atr
        out["sl_atr_1_5x"] = atr * 1.5
        out["tp_atr_2x"] = atr * 2.0
        out["tp_atr_3x"] = atr * 3.0

    bb_upper = features.get(f"{prefix}bb_upper", features.get("bb_upper", 0.0))
    bb_lower = features.get(f"{prefix}bb_lower", features.get("bb_lower", 0.0))
    if bb_upper > 0 and bb_lower > 0:
        out["bb_width"] = bb_upper - bb_lower
        out["bb_width_pct"] = ((bb_upper - bb_lower) / close * 100) if close > 0 else 0.0

    return out


# ── Base Trader ─────────────────────────────────────────────────────────────


class BaseModelTrader(ABC):
    """Abstract interface for ML/DL/RL trading models."""

    def __init__(
        self,
        model_type: str,
        model_name: str,
        model_dir: str | Path = "./models_store/traders",
    ) -> None:
        self.model_type = model_type
        self.model_name = model_name
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self._is_fitted = False
        self._performance = ModelPerformance(
            model_type=model_type,
            model_name=model_name,
        )
        self._recent_signals: list[dict[str, Any]] = []

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    @property
    def performance(self) -> ModelPerformance:
        perf = self._performance
        perf.is_fitted = self._is_fitted
        perf.recent_signals = self._recent_signals[-20:]
        return perf

    @abstractmethod
    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        ohlcv: np.ndarray | None = None,
        feature_names: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, float]:
        """Train the model. Returns training metrics."""
        ...

    @abstractmethod
    def predict(
        self,
        features: np.ndarray | dict[str, float],
        symbol: str = "",
    ) -> TradeSignal:
        """Generate a trade signal from current features."""
        ...

    @abstractmethod
    def save(self, path: Path | None = None) -> None:
        """Persist model to disk."""
        ...

    @abstractmethod
    def load(self, path: Path | None = None) -> bool:
        """Load model from disk. Returns True if successful."""
        ...

    def record_signal(self, signal: TradeSignal) -> None:
        """Track recent signals for dashboard display."""
        self._recent_signals.append(signal.to_dict())
        if len(self._recent_signals) > 50:
            self._recent_signals = self._recent_signals[-50:]

    def _default_path(self) -> Path:
        return self.model_dir / f"{self.model_name}.pkl"
