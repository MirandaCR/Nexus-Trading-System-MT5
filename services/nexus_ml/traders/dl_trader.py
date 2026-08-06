"""
DL Trader — Transformer encoder for sequence-based price prediction.

Architecture:
  1. Positional encoding + multi-head self-attention over a sequence of bars
  2. Classification head → BUY / SELL / NO_TRADE
  3. Regression heads → SL pips, TP pips

Multi-timeframe: sequences can include MTF-augmented features per bar.
Regime-aware: trains on regime-labelled data so the model learns
regime-specific patterns naturally.
"""
from __future__ import annotations

import logging
import math
import pickle
import time
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
    from torch.utils.data import DataLoader, TensorDataset

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]


# ── Transformer Model ───────────────────────────────────────────────────────


def _build_transformer(n_features: int, seq_len: int, device: str = "cpu"):
    """Build the Transformer encoder model (only if torch is available)."""
    if not _HAS_TORCH:
        return None

    class PositionalEncoding(nn.Module):
        def __init__(self, d_model: int, max_len: int = 500):
            super().__init__()
            pe = torch.zeros(max_len, d_model)
            position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
            div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
            pe[:, 0::2] = torch.sin(position * div_term)
            if d_model > 1:
                pe[:, 1::2] = torch.cos(position * div_term[:d_model // 2])
            self.register_buffer("pe", pe.unsqueeze(0))

        def forward(self, x):
            return x + self.pe[:, : x.size(1), :]

    class TradingTransformer(nn.Module):
        def __init__(self, n_features: int, seq_len: int):
            super().__init__()
            d_model = max(64, ((n_features // 16) + 1) * 16)  # round up to multiple of 16

            self.input_proj = nn.Linear(n_features, d_model)
            self.pos_enc = PositionalEncoding(d_model, seq_len)
            self.dropout = nn.Dropout(0.1)

            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=max(1, d_model // 16),
                dim_feedforward=d_model * 2,
                dropout=0.1,
                batch_first=True,
                activation="gelu",
            )
            self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=3)

            self.norm = nn.LayerNorm(d_model)

            # Classification head: 3 classes (DOWN=0, NO_TRADE=1, UP=2)
            self.cls_head = nn.Sequential(
                nn.Linear(d_model, d_model // 2),
                nn.GELU(),
                nn.Dropout(0.1),
                nn.Linear(d_model // 2, 3),
            )

            # SL/TP regression heads
            self.sl_head = nn.Sequential(
                nn.Linear(d_model, d_model // 4),
                nn.GELU(),
                nn.Linear(d_model // 4, 1),
                nn.Softplus(),  # ensure positive output
            )
            self.tp_head = nn.Sequential(
                nn.Linear(d_model, d_model // 4),
                nn.GELU(),
                nn.Linear(d_model // 4, 1),
                nn.Softplus(),
            )

        def forward(self, x):
            # x: (batch, seq_len, n_features)
            x = self.input_proj(x)
            x = self.pos_enc(x)
            x = self.dropout(x)
            x = self.transformer(x)
            x = self.norm(x)

            # Pool: use last token representation
            pooled = x[:, -1, :]

            cls_logits = self.cls_head(pooled)         # (batch, 3)
            sl_pred = self.sl_head(pooled).squeeze(-1)  # (batch,)
            tp_pred = self.tp_head(pooled).squeeze(-1)  # (batch,)

            return cls_logits, sl_pred, tp_pred

    model = TradingTransformer(n_features, seq_len).to(device)
    return model


class DLTrader(BaseModelTrader):
    """Transformer-based deep learning trader with SL/TP prediction."""

    def __init__(
        self,
        model_dir: str | Path = "./models_store/traders",
        seq_len: int = 10,
        confidence_threshold: float = 0.40,
        device: str | None = None,
    ) -> None:
        super().__init__("dl", "Transformer_SLTP", model_dir)
        self.seq_len = seq_len
        self.confidence_threshold = confidence_threshold

        if _HAS_TORCH:
            self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = "cpu"

        self._model: Any = None
        self._feature_names: list[str] = []
        self._n_features: int = 0
        self._feature_means: np.ndarray | None = None
        self._feature_stds: np.ndarray | None = None

    # ── Training ─────────────────────────────────────────────────────────

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        ohlcv: np.ndarray | None = None,
        feature_names: list[str] | None = None,
        epochs: int = 50,
        batch_size: int = 64,
        lr: float = 1e-3,
        **kwargs: Any,
    ) -> dict[str, float]:
        """Train Transformer on sequences.

        Parameters
        ----------
        X : (N, F) flat feature matrix — will be windowed into sequences
        y : (N,) binary labels (1=UP, 0=DOWN)
        ohlcv : (N, 5) for SL/TP target computation
        """
        if not _HAS_TORCH:
            logger.warning("PyTorch not available — DLTrader cannot train")
            return {"error": 1.0}

        t0 = time.time()

        if feature_names:
            self._feature_names = list(feature_names)

        self._n_features = X.shape[1]

        # Normalise features
        self._feature_means = np.nanmean(X, axis=0)
        self._feature_stds = np.nanstd(X, axis=0)
        self._feature_stds[self._feature_stds < 1e-8] = 1.0
        X_norm = (X - self._feature_means) / self._feature_stds

        # Build sequences
        train_symbol = kwargs.get("symbol", "")
        sequences, labels, sl_targets, tp_targets = self._build_sequences(
            X_norm, y, ohlcv, symbol=train_symbol,
        )

        if len(sequences) < 100:
            logger.warning("Not enough sequences for training: %d", len(sequences))
            return {"error": 1.0, "n_sequences": len(sequences)}

        # Labels: 0=DOWN, 2=UP (class 1=NO_TRADE unused in training intentionally).
        # We balance the dataset so the model sees equal UP/DOWN examples and doesn't
        # collapse to always predicting one class (the SELL/1.0 bias we observed).
        cls_labels = np.where(labels == 1, 2, 0)
        # Balance classes: oversample minority to prevent bias
        n_up = int(np.sum(cls_labels == 2))
        n_down = int(np.sum(cls_labels == 0))
        if n_up > 0 and n_down > 0 and max(n_up, n_down) / min(n_up, n_down) > 1.5:
            minority_val = 2 if n_up < n_down else 0
            minority_idx = np.where(cls_labels == minority_val)[0]
            majority_idx = np.where(cls_labels != minority_val)[0]
            target_n = len(majority_idx)
            extra = np.random.choice(minority_idx, size=target_n - len(minority_idx), replace=True)
            all_idx = np.concatenate([np.arange(len(cls_labels)), extra])
            np.random.shuffle(all_idx)
            sequences = sequences[all_idx]
            cls_labels = cls_labels[all_idx]
            sl_targets = sl_targets[all_idx]
            tp_targets = tp_targets[all_idx]
            logger.info("DL: balanced classes %d UP vs %d DOWN → %d each", n_up, n_down, target_n)

        # Tensors
        X_t = torch.FloatTensor(sequences).to(self.device)
        y_t = torch.LongTensor(cls_labels).to(self.device)
        sl_t = torch.FloatTensor(sl_targets).to(self.device)
        tp_t = torch.FloatTensor(tp_targets).to(self.device)

        dataset = TensorDataset(X_t, y_t, sl_t, tp_t)
        # Use 80/20 split
        n_train = int(len(dataset) * 0.8)
        n_val = len(dataset) - n_train
        train_ds, val_ds = torch.utils.data.random_split(dataset, [n_train, n_val])

        train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
        val_loader = DataLoader(val_ds, batch_size=batch_size)

        # Build model
        self._model = _build_transformer(self._n_features, self.seq_len, self.device)

        optimizer = optim.AdamW(self._model.parameters(), lr=lr, weight_decay=1e-4)
        scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
        cls_loss_fn = nn.CrossEntropyLoss()
        reg_loss_fn = nn.SmoothL1Loss()

        best_val_loss = float("inf")
        best_state = None

        for epoch in range(epochs):
            self._model.train()
            total_loss = 0.0

            for batch_x, batch_y, batch_sl, batch_tp in train_loader:
                optimizer.zero_grad()
                cls_logits, sl_pred, tp_pred = self._model(batch_x)

                loss_cls = cls_loss_fn(cls_logits, batch_y)
                loss_sl = reg_loss_fn(sl_pred, batch_sl)
                loss_tp = reg_loss_fn(tp_pred, batch_tp)

                loss = loss_cls + 0.3 * loss_sl + 0.3 * loss_tp
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self._model.parameters(), 1.0)
                optimizer.step()
                total_loss += loss.item()

            scheduler.step()

            # Validation
            self._model.eval()
            val_loss = 0.0
            correct = 0
            total = 0

            with torch.no_grad():
                for batch_x, batch_y, batch_sl, batch_tp in val_loader:
                    cls_logits, sl_pred, tp_pred = self._model(batch_x)
                    loss_cls = cls_loss_fn(cls_logits, batch_y)
                    val_loss += loss_cls.item()

                    preds = cls_logits.argmax(dim=1)
                    correct += (preds == batch_y).sum().item()
                    total += len(batch_y)

            val_acc = correct / total if total > 0 else 0.0

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_state = {k: v.clone() for k, v in self._model.state_dict().items()}

            if (epoch + 1) % 10 == 0:
                logger.info(
                    "DL Epoch %d/%d — train_loss=%.4f val_acc=%.4f",
                    epoch + 1, epochs, total_loss / len(train_loader), val_acc,
                )

        if best_state is not None:
            self._model.load_state_dict(best_state)

        self._is_fitted = True
        elapsed = time.time() - t0
        self._performance.training_time_sec = elapsed
        self._performance.train_samples = len(sequences)
        self._performance.last_trained = datetime.now(timezone.utc).isoformat()

        return {
            "val_acc": val_acc,
            "best_val_loss": best_val_loss,
            "n_sequences": len(sequences),
            "training_time_sec": elapsed,
        }

    def _build_sequences(
        self,
        X: np.ndarray,
        y: np.ndarray,
        ohlcv: np.ndarray | None,
        symbol: str = "",
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Window flat features into (N, seq_len, F) sequences."""
        n = len(X)
        sequences = []
        labels = []
        sl_targets = []
        tp_targets = []

        # Infer pip value from symbol or price magnitude
        if symbol:
            pip_value = get_pip_value(symbol)
        elif ohlcv is not None and len(ohlcv) > 0:
            median_price = float(np.median(ohlcv[:, 3]))
            pip_value = 0.01 if median_price > 10 else 0.0001
        else:
            pip_value = 0.0001

        for i in range(self.seq_len, n):
            seq = X[i - self.seq_len : i]
            sequences.append(seq)
            labels.append(y[i])

            # SL/TP from ATR-like measure
            if ohlcv is not None and i < len(ohlcv):
                lookback = ohlcv[max(0, i - 14) : i]
                if len(lookback) > 1:
                    ranges = lookback[:, 1] - lookback[:, 2]  # high - low
                    avg_range = float(np.mean(ranges))
                    sl_targets.append(avg_range / pip_value * 1.0)
                    tp_targets.append(avg_range / pip_value * 2.0)
                else:
                    sl_targets.append(15.0)
                    tp_targets.append(30.0)
            else:
                sl_targets.append(15.0)
                tp_targets.append(30.0)

        return (
            np.array(sequences, dtype=np.float32),
            np.array(labels),
            np.array(sl_targets, dtype=np.float32),
            np.array(tp_targets, dtype=np.float32),
        )

    # ── Prediction ───────────────────────────────────────────────────────

    def predict(
        self,
        features: np.ndarray | dict[str, float],
        symbol: str = "",
    ) -> TradeSignal:
        if not self._is_fitted or self._model is None or not _HAS_TORCH:
            return TradeSignal(
                model_type="dl", model_name=self.model_name,
                symbol=symbol, direction="NO_TRADE", confidence=0.0,
                reasoning="Model not trained or PyTorch not available",
            )

        # Features should be a sequence: (seq_len, n_features)
        if isinstance(features, dict):
            if self._feature_names:
                feat_array = np.array([features.get(k, 0.0) for k in self._feature_names])
            else:
                feat_array = np.array(list(features.values()))
            # For single-bar prediction, repeat to fill sequence
            feat_array = np.tile(feat_array, (self.seq_len, 1))
        elif features.ndim == 1:
            feat_array = np.tile(features, (self.seq_len, 1))
        elif features.ndim == 2 and features.shape[0] >= self.seq_len:
            feat_array = features[-self.seq_len:]
        else:
            feat_array = features

        # Normalise. Same guard as the RL trader: a normaliser fitted on one
        # symbol's feature set can't score another's, and an unguarded
        # broadcast error here aborts the whole autonomous cycle.
        if self._feature_means is not None and self._feature_stds is not None:
            if feat_array.shape[-1] != np.asarray(self._feature_means).shape[-1]:
                logger.warning(
                    "DL feature mismatch for %s: got %d features, model fitted on %d — abstaining",
                    symbol, feat_array.shape[-1], np.asarray(self._feature_means).shape[-1],
                )
                return TradeSignal(
                    model_type="dl", model_name=self.model_name,
                    symbol=symbol, direction="NO_TRADE", confidence=0.0,
                    reasoning="Feature-set mismatch — model trained on another symbol",
                )
            feat_array = (feat_array - self._feature_means) / self._feature_stds

        x_tensor = torch.FloatTensor(feat_array).unsqueeze(0).to(self.device)

        self._model.eval()
        with torch.no_grad():
            cls_logits, sl_pred, tp_pred = self._model(x_tensor)

        probs = torch.softmax(cls_logits, dim=1)[0].cpu().numpy()
        # 0=DOWN, 1=NO_TRADE, 2=UP
        pred_class = int(np.argmax(probs))
        confidence = float(probs[pred_class])

        if confidence < self.confidence_threshold or pred_class == 1:
            direction = "NO_TRADE"
        elif pred_class == 2:
            direction = "BUY"
        else:
            direction = "SELL"

        # Mode-aware SL/TP clamping
        try:
            from .trading_mode import get_current_config
            cfg = get_current_config()
            sl_pips = max(cfg.sl_min, min(cfg.sl_max, float(sl_pred.cpu().item())))
            tp_pips = max(cfg.tp_min, min(cfg.tp_max, float(tp_pred.cpu().item())))
        except Exception:
            sl_pips = max(3.0, float(sl_pred.cpu().item()))
            tp_pips = max(5.0, float(tp_pred.cpu().item()))

        signal = TradeSignal(
            model_type="dl",
            model_name=self.model_name,
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            features_used=self._n_features,
            reasoning=f"Transformer probs={probs.tolist()}, SL={sl_pips:.1f}, TP={tp_pips:.1f}",
        )
        self.record_signal(signal)
        return signal

    # ── Persistence ──────────────────────────────────────────────────────

    def save(self, path: Path | None = None) -> None:
        p = path or self.model_dir / "dl_transformer.pkl"
        state = {
            "model_state_dict": self._model.state_dict() if self._model else None,
            "n_features": self._n_features,
            "seq_len": self.seq_len,
            "feature_names": self._feature_names,
            "feature_means": self._feature_means,
            "feature_stds": self._feature_stds,
            "performance": self._performance,
        }
        with open(p, "wb") as f:
            pickle.dump(state, f)
        logger.info("DLTrader saved to %s", p)

    def load(self, path: Path | None = None) -> bool:
        p = path or self.model_dir / "dl_transformer.pkl"
        if not p.exists():
            return False
        try:
            with open(p, "rb") as f:
                state = pickle.load(f)
            self._n_features = state["n_features"]
            self.seq_len = state["seq_len"]
            self._feature_names = state.get("feature_names", [])
            self._feature_means = state.get("feature_means")
            self._feature_stds = state.get("feature_stds")
            self._performance = state.get("performance", self._performance)

            if _HAS_TORCH and state.get("model_state_dict"):
                self._model = _build_transformer(self._n_features, self.seq_len, self.device)
                self._model.load_state_dict(state["model_state_dict"])
                self._model.eval()
                self._is_fitted = True
                logger.info("DLTrader loaded from %s", p)
                return True
            return False
        except Exception:
            logger.error("Failed to load DLTrader from %s", p, exc_info=True)
            return False
