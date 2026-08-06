"""
Nexus Trader — Institutional Feature Engine
Puerto 8002 (nexus_features).

Publica resultados en Redis: nexus:features:institutional:{symbol}  TTL 300s
Emite por pub/sub:           nexus:ws:broadcast  (tipo institutional_features)

Bases matemáticas:
  Hurst (Mandelbrot 1963)          — régimen fractal del precio
  Entropía permutación (B&P 2002)  — predictibilidad estadística
  Lyapunov (Lorenz 1963)           — límite físico de predicción
  Matrix Profile / STUMPY (2016)   — anomalías de patrón O(n log n)
  Change Points / Ruptures (2020)  — detección de cambio de régimen
  Tick Rule (Lee & Ready 1991)     — presión de orden sin book
  Realized Vol Ratio (A&B 1998)    — expansión/compresión de volatilidad
  Ergodicity Economics (Peters 2019)— Kelly ergódico y tasa de crecimiento g
"""
from __future__ import annotations

import logging
import math

import numpy as np

logger = logging.getLogger("nexus.features.institutional")

# ── Optional imports (graceful degradation) ──────────────────────────────────

try:
    from hurst import compute_Hc
    _HURST_OK = True
except ImportError:
    _HURST_OK = False
    logger.warning("hurst not installed — pip install hurst")

try:
    import antropy
    _ANTROPY_OK = True
except ImportError:
    _ANTROPY_OK = False
    logger.warning("antropy not installed — pip install antropy")

try:
    import stumpy
    _STUMPY_OK = True
except ImportError:
    _STUMPY_OK = False
    logger.warning("stumpy not installed — pip install stumpy")

try:
    import nolds
    _NOLDS_OK = True
except ImportError:
    _NOLDS_OK = False
    logger.warning("nolds not installed — pip install nolds")

try:
    from ruptures import Pelt
    _RUPTURES_OK = True
except ImportError:
    _RUPTURES_OK = False
    logger.warning("ruptures not installed — pip install ruptures")


# ── Main class ────────────────────────────────────────────────────────────────

class NexusInstitutionalFeatures:
    """
    Motor de features institucionales para Nexus Trader.
    Todos los métodos son sin estado — instanciar una vez como singleton.
    """

    # ── 1. HURST EXPONENT ─────────────────────────────────────────────────

    def hurst_exponent(self, prices: np.ndarray, window: int = 150) -> dict:
        """
        Exponente de Hurst — régimen fractal del mercado.

        H > 0.62  → TRENDING       → preferir XGBoost + momentum
        H < 0.38  → MEAN_REVERTING → preferir LightGBM + mean-rev
        H ≈ 0.50  → NOISY          → solo rule-based, conviction +15

        Si HMM y Hurst discrepan: usar Hurst, bajar conviction 10pts.
        """
        default: dict = {
            "H": 0.5,
            "regime": "NOISY",
            "tradeable": False,
            "preferred_model": "RULES_ONLY",
            "conviction_adjustment": 15,
            "hurst_confidence": 0.0,
        }
        if not _HURST_OK or len(prices) < window:
            return default
        try:
            H, _, _ = compute_Hc(prices[-window:], kind="price", simplified=True)
        except Exception as exc:
            logger.debug("hurst.error: %s", exc)
            return default

        if H > 0.62:
            regime, model, adj = "TRENDING", "XGBoost", -5
        elif H < 0.38:
            regime, model, adj = "MEAN_REVERTING", "LightGBM", -3
        else:
            regime, model, adj = "NOISY", "RULES_ONLY", 15

        return {
            "H": float(H),
            "regime": regime,
            "tradeable": regime != "NOISY",
            "preferred_model": model,
            "conviction_adjustment": adj,
            "hurst_confidence": float(min(1.0, abs(H - 0.5) / 0.15)),
        }

    # ── 2. PERMUTATION ENTROPY ────────────────────────────────────────────

    def permutation_entropy(
        self, prices: np.ndarray, order: int = 4, window: int = 80
    ) -> dict:
        """
        Entropía de permutación (Bandt & Pompe 2002).

        PE < 0.65  → mercado estructurado → bajar umbral mínimo 5pts
        PE 0.65–0.80 → neutral           → sin ajuste
        PE > 0.80  → caótico             → subir umbral 25pts

        conviction_adjustment se suma al umbral del Árbitro:
          umbral_efectivo = 70 + conviction_adjustment
        """
        default: dict = {
            "permutation_entropy": 1.0,
            "structured": False,
            "conviction_adjustment": 25,
            "edge_quality": 0.0,
        }
        if not _ANTROPY_OK or len(prices) < window:
            return default
        try:
            pe = float(
                antropy.perm_entropy(prices[-window:], order=order, normalize=True)
            )
        except Exception:
            return default

        adj = -5 if pe < 0.65 else (0 if pe < 0.80 else 25)

        return {
            "permutation_entropy": pe,
            "structured": pe < 0.80,
            "conviction_adjustment": adj,
            "edge_quality": float(max(0.0, (0.85 - pe) / 0.85)),
        }

    # ── 3. LYAPUNOV HORIZON ───────────────────────────────────────────────

    def lyapunov_horizon(self, prices: np.ndarray) -> dict:
        """
        Exponente de Lyapunov — límite físico de predictibilidad.
        horizon_bars = 1/λ  (máxima proyección válida para los agentes).

        Para M5: horizon_bars=8 → 40 min máximo de proyección.
        Útil para limitar el TP dinámicamente.
        """
        if not _NOLDS_OK or len(prices) < 200:
            return {"horizon_bars": 10, "lyapunov_exponent": None, "reliable": False}
        try:
            log_p = np.log(np.array(prices[-200:], dtype=float) + 1e-10)
            lyap = float(nolds.lyap_r(log_p, emb_dim=6))
            horizon = int(1.0 / abs(lyap)) if lyap > 0 else 100
        except Exception:
            return {"horizon_bars": 10, "lyapunov_exponent": None, "reliable": False}

        return {
            "lyapunov_exponent": lyap,
            "horizon_bars": min(horizon, 100),
            "horizon_minutes_m5": min(horizon, 100) * 5,
            "chaotic": lyap > 0,
            "reliable": True,
        }

    # ── 4. MATRIX PROFILE NOVELTY ─────────────────────────────────────────

    def matrix_profile_novelty(
        self, prices: np.ndarray, window: int = 24
    ) -> dict:
        """
        STUMPY — novedad del patrón actual vs historia completa.

        novelty > 0.90 → modelos ML predicen fuera de dominio entrenado
          → ml_ensemble_weight reducido proporcionalmente
          → position_size_mult reducido (0.25–1.0)

        En nexus_ml: multiplicar convicción del ensemble × ml_ensemble_weight.
        """
        default: dict = {
            "novelty": 0.5,
            "novel_pattern": False,
            "ml_ensemble_weight": 1.0,
            "position_size_mult": 1.0,
        }
        if not _STUMPY_OK or len(prices) < window * 4:
            return default
        try:
            p = np.array(prices, dtype=float)
            mp = stumpy.stump(p, m=window)
            current_dist = float(mp[-1, 0])
            novelty_pct = float(np.mean(mp[:, 0] < current_dist))
        except Exception:
            return default

        ml_weight = max(0.3, 1.0 - novelty_pct * 0.7)
        if novelty_pct > 0.95:
            size_mult = 0.25
        elif novelty_pct > 0.85:
            size_mult = 0.50
        else:
            size_mult = 1.0

        return {
            "novelty": float(novelty_pct),
            "novel_pattern": novelty_pct > 0.90,
            "ml_ensemble_weight": float(ml_weight),
            "position_size_mult": float(size_mult),
        }

    # ── 5. CHANGE POINT DETECTION ─────────────────────────────────────────

    def change_point_detection(self, prices: np.ndarray) -> dict:
        """
        Ruptures (PELT) — cambio de régimen reciente.

        Si cambio < 5 bars:
          → ensemble_weight = 0.40 (distribuciones del viejo régimen inválidas)
          → disable_lstm = True
          → disable_hmm = True

        Si cambio < 15 bars:
          → ensemble_weight = 0.70
          → disable_lstm = True
        """
        default: dict = {
            "recent_shift": False,
            "bars_since": 999,
            "ensemble_weight": 1.0,
            "disable_lstm": False,
            "disable_hmm": False,
        }
        if not _RUPTURES_OK or len(prices) < 60:
            return default
        try:
            algo = Pelt(model="rbf", min_size=10, jump=2).fit(
                np.array(prices).reshape(-1, 1)
            )
            bkps = algo.predict(pen=8)
        except Exception:
            return default

        if not bkps or bkps[-1] >= len(prices):
            return default

        bars_since = len(prices) - bkps[-1]
        if bars_since < 5:
            w, lstm, hmm = 0.4, True, True
        elif bars_since < 15:
            w, lstm, hmm = 0.7, True, False
        else:
            w, lstm, hmm = 1.0, False, False

        return {
            "recent_shift": bars_since < 15,
            "bars_since": bars_since,
            "ensemble_weight": float(w),
            "disable_lstm": lstm,
            "disable_hmm": hmm,
        }

    # ── 6. TICK RULE IMBALANCE ────────────────────────────────────────────

    def tick_rule_imbalance(
        self, prices: np.ndarray, window: int = 100
    ) -> dict:
        """
        Lee & Ready (1991) — presión de orden sin datos de volumen separado.
        Input a A4 (OrderFlow) y A5/A6 (Bull/Bear).
        """
        if len(prices) < window:
            return {
                "tick_imbalance": 0.0,
                "directional_bias": "NEUTRAL",
                "strength": 0.0,
            }
        diffs = np.diff(prices[-window:])
        signs = np.sign(diffs)
        signs[signs == 0] = 1
        imb = float(np.sum(signs) / len(signs))

        if imb > 0.25:
            bias = "BUY_PRESSURE"
        elif imb < -0.25:
            bias = "SELL_PRESSURE"
        else:
            bias = "NEUTRAL"

        return {
            "tick_imbalance": imb,
            "directional_bias": bias,
            "strength": float(abs(imb)),
        }

    # ── 7. REALIZED VOLATILITY RATIO ──────────────────────────────────────

    def realized_volatility_ratio(
        self, prices: np.ndarray, fast: int = 20, slow: int = 100
    ) -> dict:
        """
        Andersen & Bollerslev (1998) — ratio RV_fast / RV_slow.

        ratio > 1.5 → expansión  → favorecer breakout
        ratio < 0.7 → compresión → favorecer mean-reversion
        Feature adicional para LightGBM y XGBoost.
        """
        if len(prices) < slow + 1:
            return {"rv_fast": 0.0, "rv_slow": 0.0, "rv_ratio": 1.0, "regime": "STABLE"}
        log_r = np.diff(np.log(np.array(prices, dtype=float) + 1e-10))
        rv_fast = float(math.sqrt(float(np.sum(log_r[-fast:] ** 2)) / fast))
        rv_slow = float(math.sqrt(float(np.sum(log_r[-slow:] ** 2)) / slow))
        ratio = rv_fast / (rv_slow + 1e-10)

        if ratio > 1.5:
            regime = "EXPANDING"
        elif ratio < 0.7:
            regime = "COMPRESSING"
        else:
            regime = "STABLE"

        return {
            "rv_fast": rv_fast,
            "rv_slow": rv_slow,
            "rv_ratio": float(ratio),
            "regime": regime,
        }

    # ── 8. ERGODIC KELLY (Peters 2019) ────────────────────────────────────

    def ergodic_kelly_sizing(
        self,
        win_rate: float,
        avg_win_pips: float,
        avg_loss_pips: float,
        account_equity: float,
        session_mult: float = 1.0,
        vol_regime: str = "NORMAL",
    ) -> dict:
        """
        Kelly Ergódico — Ole Peters, London Mathematical Laboratory (2019).

        f*_ergodic = f*_classic - σ²/(2W)

        El término σ²/(2W) penaliza la varianza del proceso multiplicativo.
        Con W=$173 (Nexus actual) esta penalización es significativa.
        Ignorarla produce sobredimensionamiento sistemático.

        Resultado: lot size con fracción conservadora ×0.25.
        """
        default: dict = {
            "lot": 0.01,
            "kelly_classic": 0.0,
            "kelly_ergodic": 0.0,
            "kelly_final": 0.0,
            "variance_penalty": 0.0,
        }
        if win_rate <= 0 or win_rate >= 1 or avg_loss_pips <= 0:
            return default

        p = float(win_rate)
        q = 1.0 - p
        b = float(avg_win_pips) / (float(avg_loss_pips) + 1e-10)

        kelly_classic = max(0.0, (p * b - q) / b)

        # Penalización ergódica: varianza del retorno multiplicativo
        variance = p * (avg_win_pips ** 2) + q * (avg_loss_pips ** 2)
        pip_value_usd = 1.0  # XAUUSD en XM: $1/pip por 0.01 lot
        ergodic_penalty = variance / (2.0 * account_equity * pip_value_usd + 1e-10)
        kelly_ergodic = max(0.0, kelly_classic - ergodic_penalty)
        kelly_final = kelly_ergodic * 0.25  # fracción conservadora

        vol_mults = {
            "LOW": 1.0, "NORMAL": 1.0,
            "MED": 0.75, "HIGH": 0.5, "EXTREME": 0.25,
        }
        vol_mult = vol_mults.get(vol_regime.upper(), 1.0)

        pip_value_per_lot = 100.0  # $100/pip para 1.0 lot XAUUSD
        base_lot = (
            (account_equity * kelly_final)
            / (avg_loss_pips * pip_value_per_lot + 1e-10)
        )
        raw_lot = base_lot * session_mult * vol_mult

        # Cap: nunca más del 2% del equity en riesgo real
        max_risk_lot = (account_equity * 0.02) / (
            avg_loss_pips * pip_value_per_lot / 100.0 + 1e-10
        )
        final_lot = round(max(0.01, min(raw_lot, max_risk_lot, 0.50)), 2)

        return {
            "lot": final_lot,
            "kelly_classic": float(kelly_classic),
            "kelly_ergodic": float(kelly_ergodic),
            "kelly_final": float(kelly_final),
            "variance_penalty": float(ergodic_penalty),
        }

    # ── 9. TEMPORAL GROWTH RATE (Peters 2019) ─────────────────────────────

    def temporal_growth_rate(
        self, equity_history: list, window: int = 30
    ) -> dict:
        """
        Tasa de crecimiento temporal g = (1/T) × log(W_T / W_0).

        Con WR=40%: el retorno aritmético puede ser positivo pero g<0.
        Esto explica las pérdidas en Nexus — el promedio aritmético miente.

        Acciones automáticas:
          g < -0.003 por 30 trades → PAUSE_SYSTEM (30 min)
          g < 0                   → REDUCE_SIZE_50PCT
          g < 0.001               → CAUTION
          g ≥ 0.001               → NORMAL
        """
        if len(equity_history) < window:
            return {
                "temporal_growth_g": None,
                "arithmetic_mean": None,
                "variance_tax": None,
                "viable": None,
                "action": "INSUFFICIENT_DATA",
            }

        recent = [float(e) for e in equity_history[-window:]]
        W0, WT = recent[0], recent[-1]

        if W0 <= 0 or WT <= 0:
            return {
                "temporal_growth_g": float("-inf"),
                "arithmetic_mean": 0.0,
                "variance_tax": 0.0,
                "viable": False,
                "action": "EMERGENCY_STOP",
            }

        g = (1.0 / window) * math.log(WT / W0)
        returns = np.diff(recent) / (np.array(recent[:-1]) + 1e-10)
        arith_mean = float(np.mean(returns))
        variance_tax = arith_mean - g

        if g < -0.003:
            action = "PAUSE_SYSTEM"
        elif g < 0:
            action = "REDUCE_SIZE_50PCT"
        elif g < 0.001:
            action = "CAUTION"
        else:
            action = "NORMAL"

        return {
            "temporal_growth_g": float(g),
            "arithmetic_mean": float(arith_mean),
            "variance_tax": float(variance_tax),
            "viable": g > 0,
            "action": action,
        }

    # ── 10. COMPUTE ALL ───────────────────────────────────────────────────

    def compute_all(
        self,
        prices: np.ndarray,
        equity_history: list,
        win_rate: float = 0.40,
        avg_win_pips: float = 10.0,
        avg_loss_pips: float = 8.0,
        account_equity: float = 191.0,
        session_mult: float = 1.0,
        vol_regime: str = "NORMAL",
    ) -> dict:
        """
        Punto de entrada único.
        Llamar cada ciclo desde nexus_features después de calcular indicadores estándar.
        Publicar resultado en Redis: nexus:features:institutional:{symbol}  TTL=300s
        """
        result: dict = {
            "hurst":           self.hurst_exponent(prices),
            "perm_entropy":    self.permutation_entropy(prices),
            "lyapunov":        self.lyapunov_horizon(prices),
            "matrix_profile":  self.matrix_profile_novelty(prices),
            "change_points":   self.change_point_detection(prices),
            "tick_imbalance":  self.tick_rule_imbalance(prices),
            "rv_ratio":        self.realized_volatility_ratio(prices),
            "ergodic_kelly":   self.ergodic_kelly_sizing(
                                   win_rate, avg_win_pips, avg_loss_pips,
                                   account_equity, session_mult, vol_regime,
                               ),
            "temporal_growth": self.temporal_growth_rate(equity_history),
        }

        # ── Score sintético 0–100 ────────────────────────────────────
        H = result["hurst"]["H"]
        pe = result["perm_entropy"]["permutation_entropy"]
        nov = result["matrix_profile"]["novelty"]
        g = result["temporal_growth"].get("temporal_growth_g") or 0.0

        score = (
            (abs(H - 0.5) / 0.5) * 40.0       # fractalidad (0–40)
            + (1.0 - pe) * 30.0                 # estructura (0–30)
            + (1.0 - nov) * 20.0                # familiaridad del patrón (0–20)
            + (10.0 if g > 0 else 0.0)           # equity creciendo (0–10)
        )
        result["operability_score"] = float(min(100.0, max(0.0, score)))

        if score > 65:
            result["recommendation"] = "TRADE_FULL"
        elif score > 40:
            result["recommendation"] = "TRADE_REDUCED"
        else:
            result["recommendation"] = "WAIT"

        return result
