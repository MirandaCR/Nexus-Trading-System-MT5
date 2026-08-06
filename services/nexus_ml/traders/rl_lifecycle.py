"""
RL Lifecycle Agent — PPO that manages open trade positions bar-by-bar.

Instead of deciding entry, this agent decides WHAT TO DO with an
already-open position every bar:
  HOLD              — do nothing, position is behaving as expected
  CLOSE_NOW         — exit immediately (adverse regime / time limit)
  MOVE_SL_BREAKEVEN — move stop-loss to entry (lock break-even)
  TAKE_PARTIAL_50PCT — close 50% of position (bank partial profit)
  TRAIL_SL_1ATR     — trail stop-loss 1 ATR behind current price

State vector (12 features):
  0  pnl_pips_norm        unrealised PnL in pips / 200
  1  bars_held_norm        bars in trade / max_hold_bars
  2  sl_distance_norm      pips to SL / 200
  3  tp_distance_norm      pips to TP / 200
  4  atr_norm              ATR / entry_price * 1000
  5  regime_score          -1=bear, 0=neutral, 1=bull
  6  mtf_alignment         -1..+1 (from confluence score / 4)
  7  spread_norm           current spread / 50
  8  signal_quality        original signal quality / 100
  9  rsi_norm              RSI / 100
  10 adx_norm              ADX / 100
  11 momentum_sign         sign of macd_histogram (-1/0/1)
"""
from __future__ import annotations

import json
import logging
import pickle
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.distributions import Categorical

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]


# ── Action space ─────────────────────────────────────────────────────────────

class LifecycleAction(IntEnum):
    HOLD               = 0
    CLOSE_NOW          = 1
    MOVE_SL_BREAKEVEN  = 2
    TAKE_PARTIAL_50PCT = 3
    TRAIL_SL_1ATR      = 4


ACTION_LABELS = {
    LifecycleAction.HOLD:               "HOLD",
    LifecycleAction.CLOSE_NOW:          "CLOSE_NOW",
    LifecycleAction.MOVE_SL_BREAKEVEN:  "MOVE_SL_BREAKEVEN",
    LifecycleAction.TAKE_PARTIAL_50PCT: "TAKE_PARTIAL_50PCT",
    LifecycleAction.TRAIL_SL_1ATR:      "TRAIL_SL_1ATR",
}

ACTION_COLORS = {
    "HOLD":               "neutral",
    "CLOSE_NOW":          "red",
    "MOVE_SL_BREAKEVEN":  "yellow",
    "TAKE_PARTIAL_50PCT": "green",
    "TRAIL_SL_1ATR":      "cyan",
}

N_ACTIONS   = len(LifecycleAction)
STATE_DIM   = 12


# ── Position state dataclass ─────────────────────────────────────────────────

@dataclass
class PositionState:
    ticket: int
    symbol: str
    side: str                  # "BUY" or "SELL"
    entry_price: float
    current_price: float
    sl_price: float
    tp_price: float
    lot_size: float
    open_time: str             # ISO
    bars_held: int
    atr: float
    regime: str                # "TRENDING_BULL" etc.
    mtf_alignment_score: float # -4..+4
    spread_pips: float
    signal_quality: float      # 0..100 original entry quality
    rsi: float
    adx: float
    macd_hist: float
    max_hold_bars: int = 15
    pip_value: float = 0.01    # XAUUSD: 0.01

    # ── Derived ──────────────────────────────────────────────────────────

    @property
    def pnl_pips(self) -> float:
        sign = 1.0 if self.side == "BUY" else -1.0
        return sign * (self.current_price - self.entry_price) / self.pip_value

    @property
    def sl_distance_pips(self) -> float:
        return abs(self.current_price - self.sl_price) / self.pip_value

    @property
    def tp_distance_pips(self) -> float:
        return abs(self.tp_price - self.current_price) / self.pip_value

    @property
    def regime_score(self) -> float:
        if "BULL" in self.regime:
            return 1.0 if self.side == "BUY" else -1.0
        if "BEAR" in self.regime:
            return -1.0 if self.side == "BUY" else 1.0
        if self.regime == "BREAKOUT":
            return 0.5
        return 0.0  # RANGING / HIGH_VOL

    def to_vector(self) -> np.ndarray:
        return np.array([
            np.clip(self.pnl_pips / 200.0, -3.0, 3.0),
            min(self.bars_held / max(self.max_hold_bars, 1), 1.0),
            np.clip(self.sl_distance_pips / 200.0, 0.0, 3.0),
            np.clip(self.tp_distance_pips / 200.0, 0.0, 3.0),
            np.clip(self.atr / max(self.entry_price, 1.0) * 1000, 0.0, 5.0),
            self.regime_score,
            np.clip(self.mtf_alignment_score / 4.0, -1.0, 1.0),
            np.clip(self.spread_pips / 50.0, 0.0, 1.0),
            self.signal_quality / 100.0,
            self.rsi / 100.0,
            self.adx / 100.0,
            float(np.sign(self.macd_hist)),
        ], dtype=np.float32)


# ── Lifecycle environment (for training) ─────────────────────────────────────

class LifecycleEnv:
    """Replay-style environment for training on historical positions.

    Each episode = one historical trade with its bar-by-bar context.
    The agent learns which management action maximises final PnL.
    """

    def __init__(
        self,
        episodes: list[dict[str, Any]],
        pip_value: float = 0.01,
        max_hold_bars: int = 15,
    ) -> None:
        self.episodes = episodes
        self.pip_value = pip_value
        self.max_hold_bars = max_hold_bars
        self._ep_idx = 0
        self._step_idx = 0
        self._ep: dict[str, Any] = {}

    # ── Gym-style API ────────────────────────────────────────────────────

    def reset(self) -> np.ndarray:
        if self._ep_idx >= len(self.episodes):
            self._ep_idx = 0
        self._ep = self.episodes[self._ep_idx]
        self._ep_idx = (self._ep_idx + 1) % len(self.episodes)
        self._step_idx = 0
        self._partial_taken = False
        self._be_moved = False
        return self._get_obs()

    def _get_obs(self) -> np.ndarray:
        bars = self._ep.get("bars", [])
        if self._step_idx >= len(bars):
            return np.zeros(STATE_DIM, dtype=np.float32)
        bar = bars[self._step_idx]
        side = self._ep.get("side", "BUY")
        entry = float(self._ep.get("entry_price", 0.0))
        current = float(bar.get("close", entry))
        sl = float(self._ep.get("sl_price", entry * 0.99))
        tp = float(self._ep.get("tp_price", entry * 1.02))

        ps = PositionState(
            ticket=0, symbol="XAUUSD", side=side,
            entry_price=entry, current_price=current,
            sl_price=sl, tp_price=tp,
            lot_size=0.01,
            open_time=self._ep.get("open_time", ""),
            bars_held=self._step_idx,
            atr=float(bar.get("atr", 5.0)),
            regime=self._ep.get("regime", "RANGING"),
            mtf_alignment_score=float(bar.get("mtf_score", 0.0)),
            spread_pips=float(bar.get("spread", 1.5)),
            signal_quality=float(self._ep.get("quality_score", 60.0)),
            rsi=float(bar.get("rsi", 50.0)),
            adx=float(bar.get("adx", 25.0)),
            macd_hist=float(bar.get("macd_hist", 0.0)),
            max_hold_bars=self.max_hold_bars,
            pip_value=self.pip_value,
        )
        return ps.to_vector()

    def step(self, action: int) -> tuple[np.ndarray, float, bool, dict]:
        bars = self._ep.get("bars", [])
        final_pnl = float(self._ep.get("final_pnl_pips", 0.0))
        n_bars = len(bars)
        bar_idx = min(self._step_idx, n_bars - 1)
        bar = bars[bar_idx] if bars else {}

        side = self._ep.get("side", "BUY")
        entry = float(self._ep.get("entry_price", 0.0))
        current = float(bar.get("close", entry))
        pnl_now = ((current - entry) if side == "BUY" else (entry - current)) / self.pip_value
        atr = float(bar.get("atr", 5.0))

        reward = 0.0
        done = False
        info: dict[str, Any] = {}

        a = LifecycleAction(action)

        if a == LifecycleAction.CLOSE_NOW:
            # Reward = actual exit PnL scaled
            reward = pnl_now / 100.0
            done = True
            info["exit_reason"] = "rl_close"

        elif a == LifecycleAction.MOVE_SL_BREAKEVEN:
            if not self._be_moved and pnl_now > atr / self.pip_value:
                # Reward for protecting profit (avoid future SL hit)
                reward = 0.1 + pnl_now / 200.0
                self._be_moved = True
            else:
                reward = -0.02  # repeated or too early

        elif a == LifecycleAction.TAKE_PARTIAL_50PCT:
            if not self._partial_taken and pnl_now > 0:
                reward = 0.3 * pnl_now / 100.0  # bank half
                self._partial_taken = True
            else:
                reward = -0.03

        elif a == LifecycleAction.TRAIL_SL_1ATR:
            if pnl_now > atr / self.pip_value:
                reward = 0.05  # prudent management
            else:
                reward = -0.01

        else:  # HOLD
            # Small holding reward proportional to how trade is progressing
            progress = pnl_now / max(abs(final_pnl), 1.0)
            reward = 0.01 * progress

        self._step_idx += 1
        if self._step_idx >= n_bars:
            done = True
            # Terminal reward = final PnL outcome
            reward += final_pnl / 100.0

        return self._get_obs(), reward, done, info


# ── PPO Network (lifecycle) ───────────────────────────────────────────────────

def _build_lifecycle_net(device: str = "cpu"):
    if not _HAS_TORCH:
        return None

    class LifecyclePPO(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.shared = nn.Sequential(
                nn.Linear(STATE_DIM, 64),
                nn.GELU(),
                nn.Linear(64, 64),
                nn.GELU(),
            )
            self.action_head = nn.Linear(64, N_ACTIONS)
            self.value_head  = nn.Linear(64, 1)

        def forward(self, x):
            h = self.shared(x)
            return self.action_head(h), self.value_head(h).squeeze(-1)

    return LifecyclePPO().to(device)


# ── Main Agent ────────────────────────────────────────────────────────────────

class RLLifecycleAgent:
    """Manages open position lifecycle using a trained PPO network.

    Usage:
        agent = RLLifecycleAgent()
        agent.load()                    # loads from disk if available
        action, probs = agent.decide(position_state)
    """

    MODEL_FILE = "rl_lifecycle.pkl"

    def __init__(self, model_dir: str | Path = "./models_store/traders") -> None:
        self.model_dir = Path(model_dir)
        self.device = "cuda" if (_HAS_TORCH and torch.cuda.is_available()) else "cpu"
        self._model: Any = None
        self._is_fitted = False
        self._trained_at: str = ""
        self._n_episodes_trained = 0

    # ── Inference ────────────────────────────────────────────────────────

    def decide(self, state: PositionState) -> tuple[str, dict[str, float]]:
        """Return (action_label, {action: probability}) for a live position."""
        if not self._is_fitted or self._model is None or not _HAS_TORCH:
            return "HOLD", {a.name: 1.0 / N_ACTIONS for a in LifecycleAction}

        obs = state.to_vector()
        obs_t = torch.FloatTensor(obs).unsqueeze(0).to(self.device)
        self._model.eval()
        with torch.no_grad():
            logits, _ = self._model(obs_t)
        probs = torch.softmax(logits, dim=1)[0].cpu().numpy()
        action_idx = int(np.argmax(probs))
        action_label = ACTION_LABELS[LifecycleAction(action_idx)]
        prob_dict = {ACTION_LABELS[LifecycleAction(i)]: float(probs[i]) for i in range(N_ACTIONS)}
        return action_label, prob_dict

    def decide_from_dict(self, d: dict[str, Any]) -> tuple[str, dict[str, float]]:
        """Build PositionState from raw dict (from Redis / API) then decide."""
        try:
            ps = PositionState(
                ticket=int(d.get("ticket", 0)),
                symbol=str(d.get("symbol", "XAUUSD")),
                side=str(d.get("side", "BUY")),
                entry_price=float(d.get("entry_price", 0.0)),
                current_price=float(d.get("current_price", 0.0)),
                sl_price=float(d.get("sl_price", 0.0)),
                tp_price=float(d.get("tp_price", 0.0)),
                lot_size=float(d.get("lot_size", 0.01)),
                open_time=str(d.get("open_time", "")),
                bars_held=int(d.get("bars_held", 0)),
                atr=float(d.get("atr", 5.0)),
                regime=str(d.get("regime", "RANGING")),
                mtf_alignment_score=float(d.get("mtf_alignment_score", 0.0)),
                spread_pips=float(d.get("spread_pips", 1.5)),
                signal_quality=float(d.get("signal_quality", 60.0)),
                rsi=float(d.get("rsi", 50.0)),
                adx=float(d.get("adx", 25.0)),
                macd_hist=float(d.get("macd_hist", 0.0)),
                max_hold_bars=int(d.get("max_hold_bars", 15)),
                pip_value=float(d.get("pip_value", 0.01)),
            )
            return self.decide(ps)
        except Exception as exc:
            logger.warning("lifecycle.decide_from_dict error: %s", exc)
            return "HOLD", {}

    # ── Training ─────────────────────────────────────────────────────────

    def train(
        self,
        episodes: list[dict[str, Any]],
        n_epochs: int = 20,
        steps_per_epoch: int = 512,
        ppo_epochs: int = 4,
        clip_ratio: float = 0.2,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
        lr: float = 3e-4,
        pip_value: float = 0.01,
        max_hold_bars: int = 15,
    ) -> dict[str, float]:
        """Train on a list of historical episode dicts.

        Each episode dict must contain:
          side, entry_price, sl_price, tp_price, final_pnl_pips,
          regime, quality_score, open_time,
          bars: list[{close, atr, rsi, adx, macd_hist, spread, mtf_score}]
        """
        if not _HAS_TORCH:
            logger.warning("rl_lifecycle: PyTorch not available, cannot train")
            return {"error": 1.0}

        if not episodes:
            logger.warning("rl_lifecycle: no episodes to train on")
            return {"error": 1.0}

        env = LifecycleEnv(episodes, pip_value=pip_value, max_hold_bars=max_hold_bars)
        self._model = _build_lifecycle_net(self.device)
        optimizer = optim.Adam(self._model.parameters(), lr=lr)

        all_ep_rewards: list[float] = []
        t0 = time.time()

        for epoch in range(n_epochs):
            obs_buf, act_buf, rew_buf, val_buf, logp_buf, done_buf = [], [], [], [], [], []
            obs = env.reset()
            ep_r = 0.0

            for _ in range(steps_per_epoch):
                obs_t = torch.FloatTensor(obs).unsqueeze(0).to(self.device)
                with torch.no_grad():
                    logits, value = self._model(obs_t)
                dist = Categorical(logits=logits)
                act = dist.sample()
                logp = dist.log_prob(act)

                obs_buf.append(obs)
                act_buf.append(act.cpu().item())
                val_buf.append(value.cpu().item())
                logp_buf.append(logp.cpu().item())

                next_obs, reward, done, _ = env.step(int(act.cpu().item()))
                rew_buf.append(reward)
                done_buf.append(done)
                ep_r += reward
                obs = next_obs

                if done:
                    all_ep_rewards.append(ep_r)
                    ep_r = 0.0
                    obs = env.reset()

            # GAE
            advantages = np.zeros(len(rew_buf), dtype=np.float32)
            last_gae = 0.0
            for t in reversed(range(len(rew_buf))):
                nv = 0.0 if (t == len(rew_buf) - 1 or done_buf[t]) else val_buf[t + 1]
                delta = rew_buf[t] + gamma * nv - val_buf[t]
                advantages[t] = last_gae = delta + gamma * gae_lambda * last_gae * (1 - done_buf[t])
            returns = advantages + np.array(val_buf)

            obs_t  = torch.FloatTensor(np.array(obs_buf)).to(self.device)
            act_t  = torch.LongTensor(act_buf).to(self.device)
            old_lp = torch.FloatTensor(logp_buf).to(self.device)
            adv_t  = torch.FloatTensor(advantages).to(self.device)
            ret_t  = torch.FloatTensor(returns).to(self.device)
            adv_t  = (adv_t - adv_t.mean()) / (adv_t.std() + 1e-8)

            for _ in range(ppo_epochs):
                logits, values = self._model(obs_t)
                dist = Categorical(logits=logits)
                lp = dist.log_prob(act_t)
                entropy = dist.entropy().mean()
                ratio = torch.exp(lp - old_lp)
                clip_adv = torch.clamp(ratio, 1 - clip_ratio, 1 + clip_ratio) * adv_t
                pol_loss = -torch.min(ratio * adv_t, clip_adv).mean()
                val_loss = ((values - ret_t) ** 2).mean()
                loss = pol_loss + 0.5 * val_loss - 0.01 * entropy
                optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self._model.parameters(), 0.5)
                optimizer.step()

            avg_r = float(np.mean(all_ep_rewards[-10:])) if all_ep_rewards else 0.0
            logger.info("lifecycle epoch %d/%d — avg_r=%.3f episodes=%d",
                        epoch + 1, n_epochs, avg_r, len(all_ep_rewards))

        self._is_fitted = True
        self._trained_at = datetime.now(timezone.utc).isoformat()
        self._n_episodes_trained = len(episodes)
        elapsed = time.time() - t0
        return {
            "avg_reward": float(np.mean(all_ep_rewards)) if all_ep_rewards else 0.0,
            "episodes": len(all_ep_rewards),
            "training_time_sec": elapsed,
        }

    # ── Persistence ──────────────────────────────────────────────────────

    def save(self, path: Path | None = None) -> None:
        p = path or self.model_dir / self.MODEL_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        state: dict[str, Any] = {
            "model_state": self._model.state_dict() if self._model else None,
            "trained_at": self._trained_at,
            "n_episodes": self._n_episodes_trained,
        }
        with open(p, "wb") as f:
            pickle.dump(state, f)
        logger.info("RLLifecycleAgent saved → %s", p)

    def load(self, path: Path | None = None) -> bool:
        p = path or self.model_dir / self.MODEL_FILE
        if not p.exists():
            logger.info("lifecycle model not found at %s — will use rule-based fallback", p)
            return False
        try:
            with open(p, "rb") as f:
                state = pickle.load(f)
            if _HAS_TORCH and state.get("model_state"):
                self._model = _build_lifecycle_net(self.device)
                self._model.load_state_dict(state["model_state"])
                self._model.eval()
                self._is_fitted = True
                self._trained_at = state.get("trained_at", "")
                self._n_episodes_trained = state.get("n_episodes", 0)
                logger.info("RLLifecycleAgent loaded from %s (%d episodes)",
                            p, self._n_episodes_trained)
                return True
        except Exception:
            logger.error("lifecycle load failed from %s", p, exc_info=True)
        return False

    # ── Rule-based fallback (no trained model) ────────────────────────────

    @staticmethod
    def rule_based_decide(state: PositionState) -> tuple[str, dict[str, float]]:
        """Deterministic fallback when the PPO model is not available."""
        pnl = state.pnl_pips
        atr = state.atr / state.pip_value
        bars_ratio = state.bars_held / max(state.max_hold_bars, 1)

        if pnl < -atr * 0.5 and "BEAR" in state.regime and state.side == "BUY":
            action = "CLOSE_NOW"
        elif pnl < -atr * 0.5 and "BULL" in state.regime and state.side == "SELL":
            action = "CLOSE_NOW"
        elif pnl > atr * 1.5 and not state.regime == "RANGING":
            action = "TAKE_PARTIAL_50PCT"
        elif pnl > atr * 0.8:
            action = "MOVE_SL_BREAKEVEN"
        elif pnl > atr * 1.2:
            action = "TRAIL_SL_1ATR"
        elif bars_ratio > 0.85:
            action = "CLOSE_NOW"
        else:
            action = "HOLD"

        uniform = 1.0 / N_ACTIONS
        probs = {ACTION_LABELS[LifecycleAction(i)]: uniform for i in range(N_ACTIONS)}
        probs[action] = 0.8  # dominant action
        return action, probs
