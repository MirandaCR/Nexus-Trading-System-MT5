"""
RL Trader — PPO agent for trading with continuous action space.

The agent observes market features and decides:
  - Action: BUY / SELL / HOLD  (discrete)
  - SL distance (continuous)
  - TP distance (continuous)

Reward function is risk-adjusted PnL: considers both profit and
drawdown penalties so the agent learns conservative position management.
"""
from __future__ import annotations

import logging
import pickle
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .base_trader import BaseModelTrader, TradeSignal, get_pip_value

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.distributions import Categorical, Normal

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]


# ── Trading Environment ─────────────────────────────────────────────────────


class TradingEnv:
    """Simplified gym-like environment for RL training on OHLCV + features.

    Mode-aware: parameters adapt to SCALPING or SWING mode.
    """

    def __init__(
        self,
        features: np.ndarray,
        ohlcv: np.ndarray,
        initial_capital: float = 10_000.0,
        spread_pips: float = 1.5,
        pip_value: float = 0.0001,
        risk_per_trade: float = 0.01,
        max_hold_bars: int = 15,       # scalping default: 15 bars
        min_hold_bars: int = 2,        # minimum hold to avoid noise exits
        lookback: int = 10,
        sl_min: float = 3.0,
        sl_max: float = 15.0,
        tp_min: float = 5.0,
        tp_max: float = 25.0,
    ) -> None:
        self.features = features
        self.ohlcv = ohlcv
        self.initial_capital = initial_capital
        self.spread_pips = spread_pips
        self.pip_value = pip_value
        self.risk_per_trade = risk_per_trade
        self.max_hold_bars = max_hold_bars
        self.min_hold_bars = min_hold_bars
        self.lookback = lookback
        self.sl_min = sl_min
        self.sl_max = sl_max
        self.tp_min = tp_min
        self.tp_max = tp_max

        self.n_bars = len(features)
        self.n_features = features.shape[1]

        # State
        self.idx = lookback
        self.capital = initial_capital
        self.position = 0       # 0=flat, 1=long, -1=short
        self.entry_price = 0.0
        self.entry_idx = 0
        self.sl_price = 0.0
        self.tp_price = 0.0
        self.total_trades = 0
        self.wins = 0
        self.equity_curve: list[float] = []

    @property
    def obs_dim(self) -> int:
        return self.n_features + 5  # features + [position, unrealised_pnl, capital_pct, bars_held, spread]

    def reset(self) -> np.ndarray:
        self.idx = self.lookback
        self.capital = self.initial_capital
        self.position = 0
        self.entry_price = 0.0
        self.entry_idx = 0
        self.sl_price = 0.0
        self.tp_price = 0.0
        self.total_trades = 0
        self.wins = 0
        self.equity_curve = [self.initial_capital]
        return self._get_obs()

    def _get_obs(self) -> np.ndarray:
        if self.idx >= self.n_bars:
            return np.zeros(self.obs_dim, dtype=np.float32)

        feats = self.features[self.idx]
        close = self.ohlcv[self.idx, 3]

        unrealised = 0.0
        if self.position != 0:
            unrealised = (close - self.entry_price) * self.position / self.pip_value

        bars_held = (self.idx - self.entry_idx) if self.position != 0 else 0
        capital_pct = self.capital / self.initial_capital

        extra = np.array([
            float(self.position),
            unrealised / 100.0,  # normalise
            capital_pct,
            bars_held / self.max_hold_bars,
            self.spread_pips / 10.0,
        ], dtype=np.float32)

        return np.concatenate([feats.astype(np.float32), extra])

    def step(self, action: int, sl_pips: float = 15.0, tp_pips: float = 30.0) -> tuple[np.ndarray, float, bool, dict]:
        """Execute one step.

        action: 0=HOLD, 1=BUY, 2=SELL
        Returns: (obs, reward, done, info)
        """
        reward = 0.0
        info: dict[str, Any] = {}

        if self.idx >= self.n_bars - 1:
            return self._get_obs(), 0.0, True, info

        close = self.ohlcv[self.idx, 3]
        high = self.ohlcv[self.idx, 1]
        low = self.ohlcv[self.idx, 2]

        # ── Check existing position ──
        if self.position != 0:
            bars_held = self.idx - self.entry_idx

            # Check SL/TP hits
            exit_price = None
            exit_reason = ""

            if self.position == 1:  # long
                if low <= self.sl_price:
                    exit_price = self.sl_price
                    exit_reason = "sl"
                elif high >= self.tp_price:
                    exit_price = self.tp_price
                    exit_reason = "tp"
            else:  # short
                if high >= self.sl_price:
                    exit_price = self.sl_price
                    exit_reason = "sl"
                elif low <= self.tp_price:
                    exit_price = self.tp_price
                    exit_reason = "tp"

            # Time exit
            if exit_price is None and bars_held >= self.max_hold_bars:
                exit_price = close
                exit_reason = "time"

            # Close on opposite signal (only after min_hold_bars)
            if (exit_price is None and bars_held >= self.min_hold_bars
                    and action != 0 and action != (1 if self.position == 1 else 2)):
                exit_price = close
                exit_reason = "reversal"

            if exit_price is not None:
                pnl = (exit_price - self.entry_price) * self.position
                pnl_pips = pnl / self.pip_value
                spread_cost = self.spread_pips * self.pip_value
                net_pnl = pnl - spread_cost

                self.capital += net_pnl * 10_000  # scaled
                self.total_trades += 1
                if net_pnl > 0:
                    self.wins += 1

                # Reward: pure normalised PnL so the agent maximises actual profit.
                # No artificial RR penalty — SL hits are part of any valid strategy.
                reward = pnl_pips / 100.0  # normalise: ±100 pips → ±1.0
                if exit_reason == "tp":
                    reward += 0.2   # small bonus for clean TP hit (consistency)
                if bars_held < self.min_hold_bars:
                    reward -= 0.1   # light penalty for premature exit only

                self.position = 0
                self.entry_price = 0.0
                info["trade_pnl"] = pnl_pips
                info["exit_reason"] = exit_reason

        # ── Enter new position ──
        if self.position == 0 and action != 0:
            sl_pips_clamped = max(self.sl_min, min(sl_pips, self.sl_max))
            tp_pips_clamped = max(self.tp_min, min(tp_pips, self.tp_max))

            if action == 1:  # BUY
                self.position = 1
                self.entry_price = close
                self.sl_price = close - sl_pips_clamped * self.pip_value
                self.tp_price = close + tp_pips_clamped * self.pip_value
            elif action == 2:  # SELL
                self.position = -1
                self.entry_price = close
                self.sl_price = close + sl_pips_clamped * self.pip_value
                self.tp_price = close - tp_pips_clamped * self.pip_value

            self.entry_idx = self.idx

        # ── Small living penalty to encourage decisive trading ──
        if self.position == 0:
            reward -= 0.001  # tiny penalty for sitting flat

        self.equity_curve.append(self.capital)
        self.idx += 1
        done = self.idx >= self.n_bars - 1

        return self._get_obs(), reward, done, info


# ── PPO Actor-Critic ─────────────────────────────────────────────────────────


def _build_ppo_net(obs_dim: int, device: str = "cpu"):
    """Build PPO actor-critic network."""
    if not _HAS_TORCH:
        return None

    class PPOActorCritic(nn.Module):
        def __init__(self, obs_dim: int):
            super().__init__()
            hidden = 128

            self.shared = nn.Sequential(
                nn.Linear(obs_dim, hidden),
                nn.GELU(),
                nn.Linear(hidden, hidden),
                nn.GELU(),
            )

            # Actor: discrete action (HOLD, BUY, SELL)
            self.action_head = nn.Linear(hidden, 3)

            # Actor: continuous SL/TP
            self.sl_mean = nn.Linear(hidden, 1)
            self.sl_log_std = nn.Parameter(torch.zeros(1))
            self.tp_mean = nn.Linear(hidden, 1)
            self.tp_log_std = nn.Parameter(torch.zeros(1))

            # Critic
            self.value_head = nn.Linear(hidden, 1)

        def forward(self, obs):
            h = self.shared(obs)
            action_logits = self.action_head(h)
            value = self.value_head(h).squeeze(-1)

            # SL/TP output: normalised [0,1] sigmoid, caller scales by config range.
            # Gold(XAUUSD): SL 50-2500 pips, TP 80-5000 pips.
            sl_mu = torch.sigmoid(self.sl_mean(h).squeeze(-1))  # 0..1
            tp_mu = torch.sigmoid(self.tp_mean(h).squeeze(-1))  # 0..1

            return action_logits, sl_mu, tp_mu, value

    return PPOActorCritic(obs_dim).to(device)


class RLTrader(BaseModelTrader):
    """PPO-based reinforcement learning trader."""

    def __init__(
        self,
        model_dir: str | Path = "./models_store/traders",
        confidence_threshold: float = 0.45,
        device: str | None = None,
    ) -> None:
        super().__init__("rl", "PPO_Trader", model_dir)
        self.confidence_threshold = confidence_threshold

        if _HAS_TORCH:
            self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = "cpu"

        self._model: Any = None
        self._n_features: int = 0
        self._obs_dim: int = 0
        self._feature_means: np.ndarray | None = None
        self._feature_stds: np.ndarray | None = None

    # ── Training ─────────────────────────────────────────────────────────

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        ohlcv: np.ndarray | None = None,
        feature_names: list[str] | None = None,
        n_epochs: int = 10,
        steps_per_epoch: int = 2048,
        ppo_epochs: int = 4,
        clip_ratio: float = 0.2,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
        lr: float = 3e-4,
        **kwargs: Any,
    ) -> dict[str, float]:
        """Train PPO agent on the environment.

        X : (N, F) features
        y : (N,) labels (used for environment, not direct supervision)
        ohlcv : (N, 5) REQUIRED for environment simulation
        """
        if not _HAS_TORCH:
            logger.warning("PyTorch not available — RLTrader cannot train")
            return {"error": 1.0}

        if ohlcv is None:
            logger.error("OHLCV data required for RL training")
            return {"error": 1.0}

        t0 = time.time()

        # Normalise features
        self._feature_means = np.nanmean(X, axis=0)
        self._feature_stds = np.nanstd(X, axis=0)
        self._feature_stds[self._feature_stds < 1e-8] = 1.0
        X_norm = (X - self._feature_means) / self._feature_stds

        self._n_features = X.shape[1]

        # Infer pip value from symbol or price magnitude
        train_symbol = kwargs.get("symbol", "")
        if train_symbol:
            pip_val = get_pip_value(train_symbol)
        elif len(ohlcv) > 0:
            median_price = float(np.median(ohlcv[:, 3]))
            pip_val = 0.01 if median_price > 10 else 0.0001
        else:
            pip_val = 0.0001

        # Create environment with mode-aware parameters
        try:
            from .trading_mode import get_current_config
            cfg = get_current_config()
            env = TradingEnv(
                X_norm, ohlcv,
                max_hold_bars=cfg.max_hold_bars,
                min_hold_bars=cfg.min_hold_bars,
                risk_per_trade=cfg.risk_per_trade,
                sl_min=cfg.sl_min, sl_max=cfg.sl_max,
                tp_min=cfg.tp_min, tp_max=cfg.tp_max,
                pip_value=pip_val,
            )
        except Exception:
            env = TradingEnv(X_norm, ohlcv, pip_value=pip_val)
        self._obs_dim = env.obs_dim

        # Build model
        self._model = _build_ppo_net(self._obs_dim, self.device)
        optimizer = optim.Adam(self._model.parameters(), lr=lr)

        all_rewards: list[float] = []
        all_wins: list[float] = []

        for epoch in range(n_epochs):
            # Collect trajectories
            obs_buf = []
            act_buf = []
            rew_buf = []
            val_buf = []
            logp_buf = []
            done_buf = []
            sl_buf = []
            tp_buf = []

            obs = env.reset()
            ep_reward = 0.0

            for step in range(steps_per_epoch):
                obs_t = torch.FloatTensor(obs).unsqueeze(0).to(self.device)

                with torch.no_grad():
                    action_logits, sl_mu, tp_mu, value = self._model(obs_t)

                dist = Categorical(logits=action_logits)
                action = dist.sample()
                logp = dist.log_prob(action)

                sl_dist = Normal(sl_mu, torch.exp(self._model.sl_log_std).clamp(0.1, 10.0))
                tp_dist = Normal(tp_mu, torch.exp(self._model.tp_log_std).clamp(0.1, 10.0))
                sl_val = sl_dist.sample()
                tp_val = tp_dist.sample()

                sl_pips = float(sl_val.cpu().item())
                tp_pips = float(tp_val.cpu().item())

                obs_buf.append(obs)
                act_buf.append(action.cpu().item())
                val_buf.append(value.cpu().item())
                logp_buf.append(logp.cpu().item())
                sl_buf.append(sl_pips)
                tp_buf.append(tp_pips)

                next_obs, reward, done, info = env.step(
                    int(action.cpu().item()),
                    sl_pips=sl_pips,
                    tp_pips=tp_pips,
                )
                rew_buf.append(reward)
                done_buf.append(done)
                ep_reward += reward
                obs = next_obs

                if done:
                    obs = env.reset()
                    all_rewards.append(ep_reward)
                    all_wins.append(env.wins / max(env.total_trades, 1))
                    ep_reward = 0.0

            # Compute advantages (GAE)
            advantages = np.zeros(len(rew_buf), dtype=np.float32)
            last_gae = 0.0
            for t in reversed(range(len(rew_buf))):
                if t == len(rew_buf) - 1 or done_buf[t]:
                    next_val = 0.0
                else:
                    next_val = val_buf[t + 1]
                delta = rew_buf[t] + gamma * next_val - val_buf[t]
                advantages[t] = last_gae = delta + gamma * gae_lambda * last_gae * (1 - done_buf[t])

            returns = advantages + np.array(val_buf)

            # Convert to tensors
            obs_t = torch.FloatTensor(np.array(obs_buf)).to(self.device)
            act_t = torch.LongTensor(act_buf).to(self.device)
            old_logp_t = torch.FloatTensor(logp_buf).to(self.device)
            adv_t = torch.FloatTensor(advantages).to(self.device)
            ret_t = torch.FloatTensor(returns).to(self.device)

            adv_t = (adv_t - adv_t.mean()) / (adv_t.std() + 1e-8)

            # PPO update
            for _ in range(ppo_epochs):
                action_logits, sl_mu, tp_mu, values = self._model(obs_t)
                dist = Categorical(logits=action_logits)
                logp = dist.log_prob(act_t)
                entropy = dist.entropy().mean()

                ratio = torch.exp(logp - old_logp_t)
                clip_adv = torch.clamp(ratio, 1 - clip_ratio, 1 + clip_ratio) * adv_t
                policy_loss = -torch.min(ratio * adv_t, clip_adv).mean()
                value_loss = ((values - ret_t) ** 2).mean()
                loss = policy_loss + 0.5 * value_loss - 0.01 * entropy

                optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self._model.parameters(), 0.5)
                optimizer.step()

            avg_reward = float(np.mean(all_rewards[-10:])) if all_rewards else 0.0
            avg_wr = float(np.mean(all_wins[-10:])) if all_wins else 0.0
            logger.info(
                "RL Epoch %d/%d — avg_reward=%.3f avg_win_rate=%.3f",
                epoch + 1, n_epochs, avg_reward, avg_wr,
            )

        self._is_fitted = True
        elapsed = time.time() - t0
        self._performance.training_time_sec = elapsed
        self._performance.train_samples = len(X)
        self._performance.last_trained = datetime.now(timezone.utc).isoformat()

        return {
            "avg_reward": float(np.mean(all_rewards)) if all_rewards else 0.0,
            "avg_win_rate": float(np.mean(all_wins)) if all_wins else 0.0,
            "episodes": len(all_rewards),
            "training_time_sec": elapsed,
        }

    # ── Prediction ───────────────────────────────────────────────────────

    def predict(
        self,
        features: np.ndarray | dict[str, float],
        symbol: str = "",
    ) -> TradeSignal:
        if not self._is_fitted or self._model is None or not _HAS_TORCH:
            return TradeSignal(
                model_type="rl", model_name=self.model_name,
                symbol=symbol, direction="NO_TRADE", confidence=0.0,
                reasoning="Model not trained or PyTorch not available",
            )

        if isinstance(features, dict):
            # Keep only numeric features — categorical values (e.g. the
            # trading session "NEW_YORK") are not part of the trained vector
            # and would raise "could not convert string to float".
            # NOTE: bools are kept (float(True)=1.0) to match the training
            # builder in _fetch_training_data, which treats bool as numeric.
            feat_array = np.array(
                [
                    float(v)
                    for v in features.values()
                    if isinstance(v, (int, float))
                ],
                dtype=np.float32,
            )
        else:
            feat_array = features.astype(np.float32)

        # Normalise. The normaliser is fitted per training run, so a model
        # trained on one symbol's feature set (gold: 41 cols) cannot score
        # another's (BTC: 32) — broadcasting would raise and kill the whole
        # autonomous cycle. Abstain instead so the other models still vote.
        if self._feature_means is not None and self._feature_stds is not None:
            if feat_array.shape[0] != self._feature_means.shape[0]:
                logger.warning(
                    "RL feature mismatch for %s: got %d features, model fitted on %d — abstaining",
                    symbol, feat_array.shape[0], self._feature_means.shape[0],
                )
                return TradeSignal(
                    model_type="rl", model_name=self.model_name,
                    symbol=symbol, direction="NO_TRADE", confidence=0.0,
                    reasoning=(
                        f"Feature-set mismatch ({feat_array.shape[0]} vs "
                        f"{self._feature_means.shape[0]}) — model trained on another symbol"
                    ),
                )
            feat_array = (feat_array - self._feature_means) / self._feature_stds

        # Pad with position state (flat by default)
        extra = np.array([0.0, 0.0, 1.0, 0.0, 0.15], dtype=np.float32)
        obs = np.concatenate([feat_array, extra])

        obs_t = torch.FloatTensor(obs).unsqueeze(0).to(self.device)

        self._model.eval()
        with torch.no_grad():
            action_logits, sl_mu, tp_mu, value = self._model(obs_t)

        probs = torch.softmax(action_logits, dim=1)[0].cpu().numpy()
        action = int(np.argmax(probs))
        confidence = float(probs[action])

        if action == 0 or confidence < self.confidence_threshold:
            direction = "NO_TRADE"
        elif action == 1:
            direction = "BUY"
        else:
            direction = "SELL"

        # Scale normalised [0,1] output to the mode-aware SL/TP range.
        try:
            from .trading_mode import get_current_config
            cfg = get_current_config()
            sl_norm = float(sl_mu.cpu().item())
            tp_norm = float(tp_mu.cpu().item())
            sl_pips = cfg.sl_min + sl_norm * (cfg.sl_max - cfg.sl_min)
            tp_pips = cfg.tp_min + tp_norm * (cfg.tp_max - cfg.tp_min)
        except Exception:
            sl_norm = float(sl_mu.cpu().item())
            tp_norm = float(tp_mu.cpu().item())
            sl_pips = 50.0 + sl_norm * 950.0   # 50..1000 pips fallback
            tp_pips = 80.0 + tp_norm * 1920.0  # 80..2000 pips fallback

        signal = TradeSignal(
            model_type="rl",
            model_name=self.model_name,
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            features_used=len(feat_array),
            reasoning=f"PPO probs=[{probs[0]:.3f},{probs[1]:.3f},{probs[2]:.3f}] "
                      f"value={float(value.cpu().item()):.3f} SL={sl_pips:.1f} TP={tp_pips:.1f}",
        )
        self.record_signal(signal)
        return signal

    # ── Persistence ──────────────────────────────────────────────────────

    def save(self, path: Path | None = None) -> None:
        p = path or self.model_dir / "rl_ppo.pkl"
        state = {
            "model_state_dict": self._model.state_dict() if self._model else None,
            "obs_dim": self._obs_dim,
            "n_features": self._n_features,
            "feature_means": self._feature_means,
            "feature_stds": self._feature_stds,
            "performance": self._performance,
        }
        with open(p, "wb") as f:
            pickle.dump(state, f)
        logger.info("RLTrader saved to %s", p)

    def load(self, path: Path | None = None) -> bool:
        p = path or self.model_dir / "rl_ppo.pkl"
        if not p.exists():
            return False
        try:
            with open(p, "rb") as f:
                state = pickle.load(f)
            self._obs_dim = state["obs_dim"]
            self._n_features = state["n_features"]
            self._feature_means = state.get("feature_means")
            self._feature_stds = state.get("feature_stds")
            self._performance = state.get("performance", self._performance)

            if _HAS_TORCH and state.get("model_state_dict"):
                self._model = _build_ppo_net(self._obs_dim, self.device)
                self._model.load_state_dict(state["model_state_dict"])
                self._model.eval()
                self._is_fitted = True
                logger.info("RLTrader loaded from %s", p)
                return True
            return False
        except Exception:
            logger.error("Failed to load RLTrader from %s", p, exc_info=True)
            return False
