"""
Nexus Trader — Parallel Model Trading System.

Three independent trading engines running alongside GenAI agents:
  - ML Trader:  XGBoost/LightGBM with SL/TP-aware feature engineering
  - DL Trader:  Transformer encoder for sequence-based prediction
  - RL Trader:  PPO agent learning optimal entry/exit/sizing

Supports SCALPING ↔ SWING mode switching with full parameter adaptation:
  - Dynamic SL/TP based on ATR, session, and volatility regime
  - Session-aware lot sizing and trade filtering
  - Multi-timeframe confirmation pipeline
  - GenAI API rate limiting for cost control
  - Auto-retraining scheduler (4h scalping / 24h swing)
"""
from .base_trader import BaseModelTrader, TradeSignal, ModelPerformance
from .ml_trader import MLTrader
from .dl_trader import DLTrader
from .rl_trader import RLTrader
from .trading_mode import (
    TradingMode,
    Session,
    TradingModeConfig,
    GenAIRateLimiter,
    get_current_mode,
    get_current_config,
    DynamicTFSelection,
    select_operational_tf,
    get_confirmation_tfs_for,
    detect_vol_regime_from_features,
    switch_mode,
    detect_session,
    compute_dynamic_sl_tp,
    compute_dynamic_lots,
    mtf_confirm_signal,
    spread_filter_ok,
)
from .auto_retrain import AutoRetrainScheduler
from .sl_tp_backtester import SLTPBacktester, SLTPBacktestResult

__all__ = [
    "BaseModelTrader",
    "TradeSignal",
    "ModelPerformance",
    "MLTrader",
    "DLTrader",
    "RLTrader",
    "TradingMode",
    "Session",
    "TradingModeConfig",
    "GenAIRateLimiter",
    "get_current_mode",
    "get_current_config",
    "DynamicTFSelection",
    "select_operational_tf",
    "get_confirmation_tfs_for",
    "detect_vol_regime_from_features",
    "switch_mode",
    "detect_session",
    "compute_dynamic_sl_tp",
    "compute_dynamic_lots",
    "mtf_confirm_signal",
    "spread_filter_ok",
    "AutoRetrainScheduler",
    "SLTPBacktester",
    "SLTPBacktestResult",
]
