"""
HMM Market Regime Detection — Gaussian Hidden Markov Model.

Detects market regimes (TRENDING_UP, TRENDING_DOWN, RANGING, HIGH_VOLATILITY)
using returns, volatility, and volume features. The HMM captures latent
state transitions that rule-based approaches miss.
"""
from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

try:
    from hmmlearn.hmm import GaussianHMM

    _HMM_AVAILABLE = True
except ImportError:
    GaussianHMM = None  # type: ignore[assignment,misc]
    _HMM_AVAILABLE = False
    logger.warning("hmmlearn not installed — HMM regime detection disabled")


REGIME_NAMES = ["TRENDING_UP", "TRENDING_DOWN", "RANGING", "HIGH_VOLATILITY"]

N_STATES = len(REGIME_NAMES)

_FEATURE_KEYS = ["returns", "volatility_ratio", "volume_ratio", "rsi_norm", "adx_norm"]


@dataclass
class RegimeInfo:
    """Result container for a single HMM regime prediction."""

    regime: str = "UNKNOWN"
    confidence: float = 0.0
    state_probabilities: dict[str, float] = field(default_factory=dict)
    suggested_bias: str = "NEUTRAL"  # LONG / SHORT / NEUTRAL
    volatility_state: str = "MEDIUM"  # LOW / MEDIUM / HIGH / EXTREME


class HMMRegimeDetector:
    """Gaussian HMM for market regime detection.

    Hidden states are mapped to regime labels by inspecting the learned
    emission parameters (mean return and mean volatility for each state).
    """

    def __init__(
        self,
        n_states: int = N_STATES,
        covariance_type: str = "full",
        n_iter: int = 200,
        random_state: int = 42,
    ) -> None:
        self._n_states = n_states
        self._covariance_type = covariance_type
        self._n_iter = n_iter
        self._random_state = random_state

        self._model: GaussianHMM | None = None
        self._is_fitted = False
        self._state_to_regime: dict[int, str] = {}
        self._feature_keys: list[str] = list(_FEATURE_KEYS)

    # ── Feature extraction ────────────────────────────────────────────────

    @staticmethod
    def extract_features_from_ohlcv(ohlcv: np.ndarray) -> np.ndarray:
        """Build the observation matrix from an OHLCV array (N×5).

        Columns: open, high, low, close, volume.
        Returns an (N-1)×5 feature matrix with rows:
            [returns, volatility_ratio, volume_ratio, rsi_norm, adx_norm]
        """
        if ohlcv.ndim != 2 or ohlcv.shape[1] < 5:
            raise ValueError("ohlcv must be (N, 5) with columns [O, H, L, C, V]")

        close = ohlcv[:, 3].astype(np.float64)
        high = ohlcv[:, 1].astype(np.float64)
        low = ohlcv[:, 2].astype(np.float64)
        volume = ohlcv[:, 4].astype(np.float64)

        returns = np.diff(np.log(np.maximum(close, 1e-10)))

        atr = high[1:] - low[1:]
        atr_mean = np.mean(atr) if np.mean(atr) > 0 else 1e-10
        volatility_ratio = atr / atr_mean

        vol_mean = np.mean(volume[1:]) if np.mean(volume[1:]) > 0 else 1e-10
        volume_ratio = volume[1:] / vol_mean

        rsi_norm = _compute_rsi(close)[1:] / 100.0

        adx_norm = _compute_adx(high, low, close)[1:] / 100.0

        obs = np.column_stack([
            returns,
            volatility_ratio,
            volume_ratio,
            rsi_norm,
            adx_norm,
        ])

        obs = np.nan_to_num(obs, nan=0.0, posinf=3.0, neginf=-3.0)
        return obs

    @staticmethod
    def build_observation_from_features(features: dict[str, float]) -> np.ndarray:
        """Build a single-row observation from a features dict (live prediction)."""
        close = float(features.get("close", 0))
        prev_close = float(features.get("prev_close", close))
        ret = np.log(max(close, 1e-10) / max(prev_close, 1e-10)) if prev_close > 0 else 0.0

        atr = float(features.get("atr", features.get("atr_14", 0)))
        atr_sma = float(features.get("atr_sma", features.get("atr_20_sma", atr or 1)))
        vol_ratio = atr / atr_sma if atr_sma > 0 else 1.0

        volume = float(features.get("volume", 0))
        vol_sma = float(features.get("volume_sma", features.get("volume_sma_20", volume or 1)))
        volume_ratio = volume / vol_sma if vol_sma > 0 else 1.0

        rsi = float(features.get("rsi", 50)) / 100.0
        adx = float(features.get("adx", 25)) / 100.0

        obs = np.array([[ret, vol_ratio, volume_ratio, rsi, adx]], dtype=np.float64)
        return np.nan_to_num(obs, nan=0.0, posinf=3.0, neginf=-3.0)

    # ── Training ──────────────────────────────────────────────────────────

    def fit(
        self,
        ohlcv: np.ndarray,
        features: dict[str, np.ndarray] | None = None,
    ) -> dict[str, Any]:
        """Train on OHLCV data (or pre-computed feature arrays).

        Returns training metadata including log-likelihood and state mapping.
        """
        if not _HMM_AVAILABLE:
            raise RuntimeError("hmmlearn is not installed")

        if features is not None:
            obs = np.column_stack([features[k] for k in self._feature_keys])
            obs = np.nan_to_num(obs, nan=0.0, posinf=3.0, neginf=-3.0)
        else:
            obs = self.extract_features_from_ohlcv(ohlcv)

        if len(obs) < self._n_states * 10:
            raise ValueError(
                f"Need at least {self._n_states * 10} observations, got {len(obs)}"
            )

        self._model = GaussianHMM(
            n_components=self._n_states,
            covariance_type=self._covariance_type,
            n_iter=self._n_iter,
            random_state=self._random_state,
            verbose=False,
        )
        self._model.fit(obs)
        self._assign_regime_labels()
        self._is_fitted = True

        score = float(self._model.score(obs))
        logger.info(
            "HMM fitted: %d states, %d observations, log-likelihood=%.2f",
            self._n_states, len(obs), score,
        )
        logger.info("State→Regime mapping: %s", self._state_to_regime)

        return {
            "n_states": self._n_states,
            "n_observations": len(obs),
            "log_likelihood": score,
            "state_mapping": dict(self._state_to_regime),
            "converged": getattr(self._model, "monitor_", None) is not None
            and self._model.monitor_.converged,
        }

    def _assign_regime_labels(self) -> None:
        """Map hidden states to named regimes via emission parameters.

        Strategy:
        - Highest mean volatility_ratio → HIGH_VOLATILITY
        - Highest mean return (of remaining) → TRENDING_UP
        - Lowest mean return (of remaining) → TRENDING_DOWN
        - Whatever is left → RANGING
        """
        means = self._model.means_  # (n_states, n_features)
        remaining = set(range(self._n_states))
        mapping: dict[int, str] = {}

        vol_idx = 1  # volatility_ratio column
        ret_idx = 0  # returns column

        vol_means = means[:, vol_idx]
        high_vol_state = int(np.argmax(vol_means))
        mapping[high_vol_state] = "HIGH_VOLATILITY"
        remaining.discard(high_vol_state)

        ret_means = {s: means[s, ret_idx] for s in remaining}
        trend_up_state = max(ret_means, key=ret_means.get)  # type: ignore[arg-type]
        mapping[trend_up_state] = "TRENDING_UP"
        remaining.discard(trend_up_state)

        trend_down_state = min(ret_means, key=ret_means.get)  # type: ignore[arg-type]
        if trend_down_state in remaining:
            mapping[trend_down_state] = "TRENDING_DOWN"
            remaining.discard(trend_down_state)

        for s in remaining:
            mapping[s] = "RANGING"

        self._state_to_regime = mapping

    # ── Prediction ────────────────────────────────────────────────────────

    def predict_regime(
        self, features: dict[str, float]
    ) -> tuple[str, float, dict[str, float]]:
        """Predict the current regime from a live features dict.

        Returns:
            (regime_name, confidence, state_probabilities)
        """
        if not self._is_fitted or self._model is None:
            return "UNKNOWN", 0.0, {}

        obs = self.build_observation_from_features(features)
        try:
            posteriors = self._model.predict_proba(obs)[0]
        except Exception:
            state = int(self._model.predict(obs)[0])
            posteriors = np.zeros(self._n_states)
            posteriors[state] = 1.0

        best_state = int(np.argmax(posteriors))
        regime = self._state_to_regime.get(best_state, "UNKNOWN")
        confidence = float(posteriors[best_state])

        state_probs = {
            self._state_to_regime.get(i, f"STATE_{i}"): float(posteriors[i])
            for i in range(self._n_states)
        }

        return regime, confidence, state_probs

    def predict_regime_info(self, features: dict[str, float]) -> RegimeInfo:
        """Full regime info including bias and volatility classification."""
        regime, confidence, state_probs = self.predict_regime(features)

        if regime == "TRENDING_UP":
            bias = "LONG"
        elif regime == "TRENDING_DOWN":
            bias = "SHORT"
        else:
            bias = "NEUTRAL"

        vol_prob = state_probs.get("HIGH_VOLATILITY", 0.0)
        atr_ratio = float(features.get("atr_normalized", 1.0))
        if vol_prob > 0.6 or atr_ratio > 1.8:
            vol_state = "EXTREME"
        elif vol_prob > 0.3 or atr_ratio > 1.3:
            vol_state = "HIGH"
        elif atr_ratio < 0.6:
            vol_state = "LOW"
        else:
            vol_state = "MEDIUM"

        return RegimeInfo(
            regime=regime,
            confidence=confidence,
            state_probabilities=state_probs,
            suggested_bias=bias,
            volatility_state=vol_state,
        )

    # ── Transition matrix ─────────────────────────────────────────────────

    def get_transition_matrix(self) -> np.ndarray:
        """Return the state transition probability matrix (n_states × n_states)."""
        if not self._is_fitted or self._model is None:
            return np.array([])
        return self._model.transmat_.copy()

    def get_labelled_transition_matrix(self) -> dict[str, dict[str, float]]:
        """Transition matrix with regime names as keys for readability."""
        if not self._is_fitted or self._model is None:
            return {}
        transmat = self._model.transmat_
        result: dict[str, dict[str, float]] = {}
        for i in range(self._n_states):
            from_regime = self._state_to_regime.get(i, f"STATE_{i}")
            result[from_regime] = {
                self._state_to_regime.get(j, f"STATE_{j}"): round(float(transmat[i, j]), 4)
                for j in range(self._n_states)
            }
        return result

    # ── Persistence ───────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        """Pickle the entire detector (model + label mapping)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "model": self._model,
            "state_to_regime": self._state_to_regime,
            "is_fitted": self._is_fitted,
            "n_states": self._n_states,
            "covariance_type": self._covariance_type,
            "feature_keys": self._feature_keys,
        }
        with open(path, "wb") as f:
            pickle.dump(state, f, protocol=pickle.HIGHEST_PROTOCOL)
        logger.info("HMM regime model saved to %s", path)

    def load(self, path: str | Path) -> None:
        """Load a previously saved detector."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"No HMM model file at {path}")
        with open(path, "rb") as f:
            state = pickle.load(f)  # noqa: S301
        self._model = state["model"]
        self._state_to_regime = state["state_to_regime"]
        self._is_fitted = state["is_fitted"]
        self._n_states = state.get("n_states", N_STATES)
        self._covariance_type = state.get("covariance_type", "full")
        self._feature_keys = state.get("feature_keys", list(_FEATURE_KEYS))
        logger.info("HMM regime model loaded from %s", path)

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    @property
    def state_mapping(self) -> dict[int, str]:
        return dict(self._state_to_regime)

    @staticmethod
    def is_available() -> bool:
        return _HMM_AVAILABLE


# ── Private helpers ───────────────────────────────────────────────────────────


def _compute_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder RSI — returns array same length as *close* (first values padded)."""
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    avg_gain = np.zeros_like(close)
    avg_loss = np.zeros_like(close)
    avg_gain[period] = np.mean(gain[1 : period + 1])
    avg_loss[period] = np.mean(loss[1 : period + 1])

    for i in range(period + 1, len(close)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period

    rs = np.divide(avg_gain, avg_loss, out=np.ones_like(avg_gain), where=avg_loss > 0)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi[:period] = 50.0
    return rsi


def _compute_adx(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """Simplified ADX — returns array same length as *close* (padded)."""
    n = len(close)
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ),
    )
    plus_dm = np.where(
        (high[1:] - high[:-1]) > (low[:-1] - low[1:]),
        np.maximum(high[1:] - high[:-1], 0),
        0.0,
    )
    minus_dm = np.where(
        (low[:-1] - low[1:]) > (high[1:] - high[:-1]),
        np.maximum(low[:-1] - low[1:], 0),
        0.0,
    )

    def _ema(arr: np.ndarray, p: int) -> np.ndarray:
        out = np.zeros_like(arr)
        out[0] = arr[0]
        alpha = 1.0 / p
        for i in range(1, len(arr)):
            out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
        return out

    atr_s = _ema(tr, period)
    plus_di = 100.0 * _ema(plus_dm, period) / np.maximum(atr_s, 1e-10)
    minus_di = 100.0 * _ema(minus_dm, period) / np.maximum(atr_s, 1e-10)

    dx = 100.0 * np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-10)
    adx_raw = _ema(dx, period)

    adx = np.full(n, 25.0)
    adx[1:] = adx_raw
    return adx
