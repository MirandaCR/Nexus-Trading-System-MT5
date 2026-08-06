"""
LSTM + attention model for temporal sequence prediction.

Takes the last N bars of feature vectors and predicts next-bar direction
probability.  Uses PyTorch with a multi-head self-attention layer between
stacked LSTM cells and a fully-connected head.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

logger = logging.getLogger(__name__)

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

DEFAULT_HPARAMS: dict[str, Any] = {
    "seq_len": 20,
    "hidden_size": 64,
    "num_layers": 2,
    "num_heads": 4,
    "dropout": 0.3,
    "fc_hidden": 32,
    "lr": 1e-3,
    "weight_decay": 1e-4,
    "batch_size": 64,
    "epochs": 80,
    "patience": 12,
}


# ── Network architecture ─────────────────────────────────────────────────────

class _LSTMAttentionNet(nn.Module):
    """Stacked LSTM → multi-head self-attention → dense → sigmoid."""

    def __init__(
        self,
        input_size: int,
        hidden_size: int = 64,
        num_layers: int = 2,
        num_heads: int = 4,
        dropout: float = 0.3,
        fc_hidden: int = 32,
    ) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.layer_norm = nn.LayerNorm(hidden_size)
        self.attention = nn.MultiheadAttention(
            embed_dim=hidden_size,
            num_heads=num_heads,
            dropout=dropout,
            batch_first=True,
        )
        self.head = nn.Sequential(
            nn.Linear(hidden_size, fc_hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(fc_hidden, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # (B, T, F)
        lstm_out, _ = self.lstm(x)                        # (B, T, H)
        normed = self.layer_norm(lstm_out)
        attn_out, _ = self.attention(normed, normed, normed)  # (B, T, H)
        context = attn_out[:, -1, :]                      # last timestep
        return self.head(context).squeeze(-1)              # (B,)


# ── Public wrapper ────────────────────────────────────────────────────────────

class LSTMSequenceModel:
    """Train / predict wrapper around _LSTMAttentionNet."""

    def __init__(self, hparams: dict[str, Any] | None = None) -> None:
        self.hp = {**DEFAULT_HPARAMS, **(hparams or {})}
        self._net: _LSTMAttentionNet | None = None
        self._is_fitted = False
        self._train_metrics: dict[str, float] = {}
        self._input_size: int = 0

    # ── Training ──────────────────────────────────────────────────────────

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        val_split: float = 0.15,
    ) -> dict[str, float]:
        """Train on sequences shaped (N, seq_len, features).

        y is binary: 1=UP, 0=DOWN.
        """
        assert X.ndim == 3, "X must be (N, seq_len, n_features)"
        self._input_size = X.shape[2]

        split = int(len(X) * (1 - val_split))
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]

        train_ds = TensorDataset(
            torch.tensor(X_train, dtype=torch.float32),
            torch.tensor(y_train, dtype=torch.float32),
        )
        val_ds = TensorDataset(
            torch.tensor(X_val, dtype=torch.float32),
            torch.tensor(y_val, dtype=torch.float32),
        )
        train_dl = DataLoader(train_ds, batch_size=self.hp["batch_size"], shuffle=True)
        val_dl = DataLoader(val_ds, batch_size=self.hp["batch_size"])

        self._net = _LSTMAttentionNet(
            input_size=self._input_size,
            hidden_size=self.hp["hidden_size"],
            num_layers=self.hp["num_layers"],
            num_heads=self.hp["num_heads"],
            dropout=self.hp["dropout"],
            fc_hidden=self.hp["fc_hidden"],
        ).to(DEVICE)

        optimizer = torch.optim.Adam(
            self._net.parameters(),
            lr=self.hp["lr"],
            weight_decay=self.hp["weight_decay"],
        )
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode="min", factor=0.5, patience=5,
        )
        criterion = nn.BCELoss()

        best_val_loss = float("inf")
        patience_counter = 0
        best_state: dict[str, Any] = {}

        for epoch in range(self.hp["epochs"]):
            # --- train ---
            self._net.train()
            train_loss = 0.0
            for xb, yb in train_dl:
                xb, yb = xb.to(DEVICE), yb.to(DEVICE)
                optimizer.zero_grad()
                preds = self._net(xb)
                loss = criterion(preds, yb)
                loss.backward()
                nn.utils.clip_grad_norm_(self._net.parameters(), max_norm=1.0)
                optimizer.step()
                train_loss += loss.item() * len(xb)
            train_loss /= len(train_ds)

            # --- validate ---
            val_loss, val_acc = self._evaluate(val_dl, criterion)
            scheduler.step(val_loss)

            if (epoch + 1) % 10 == 0:
                logger.info(
                    "Epoch %3d/%d — train_loss=%.4f  val_loss=%.4f  val_acc=%.4f",
                    epoch + 1, self.hp["epochs"], train_loss, val_loss, val_acc,
                )

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                best_state = {k: v.cpu().clone() for k, v in self._net.state_dict().items()}
            else:
                patience_counter += 1
                if patience_counter >= self.hp["patience"]:
                    logger.info("Early stopping at epoch %d", epoch + 1)
                    break

        if best_state:
            self._net.load_state_dict(best_state)
        self._net.to(DEVICE)
        self._is_fitted = True

        final_loss, final_acc = self._evaluate(val_dl, criterion)
        self._train_metrics = {
            "val_loss": final_loss,
            "val_accuracy": final_acc,
            "best_val_loss": best_val_loss,
        }
        logger.info("LSTM training done: %s", self._train_metrics)
        return self._train_metrics

    def _evaluate(
        self,
        dl: DataLoader,
        criterion: nn.Module,
    ) -> tuple[float, float]:
        assert self._net is not None
        self._net.eval()
        total_loss = 0.0
        correct = 0
        total = 0
        with torch.no_grad():
            for xb, yb in dl:
                xb, yb = xb.to(DEVICE), yb.to(DEVICE)
                preds = self._net(xb)
                total_loss += criterion(preds, yb).item() * len(xb)
                correct += ((preds >= 0.5).float() == yb).sum().item()
                total += len(yb)
        return total_loss / max(total, 1), correct / max(total, 1)

    # ── Prediction ────────────────────────────────────────────────────────

    def predict(self, features: np.ndarray) -> tuple[int, float]:
        """Predict on a single sequence (seq_len, n_features) or batch.

        Returns (direction, probability) for the first sample.
        """
        if not self._is_fitted or self._net is None:
            raise RuntimeError("Model not fitted — call train() first")

        if features.ndim == 2:
            features = features[np.newaxis, ...]  # add batch dim

        self._net.eval()
        with torch.no_grad():
            x = torch.tensor(features, dtype=torch.float32).to(DEVICE)
            prob = self._net(x).cpu().numpy()[0]

        direction = int(prob >= 0.5)
        confidence = float(prob) if direction == 1 else float(1.0 - prob)
        return direction, confidence

    def predict_batch(self, X: np.ndarray) -> list[tuple[int, float]]:
        if not self._is_fitted or self._net is None:
            raise RuntimeError("Model not fitted — call train() first")

        self._net.eval()
        with torch.no_grad():
            xt = torch.tensor(X, dtype=torch.float32).to(DEVICE)
            probs = self._net(xt).cpu().numpy()

        results = []
        for p in probs:
            d = int(p >= 0.5)
            c = float(p) if d == 1 else float(1.0 - p)
            results.append((d, c))
        return results

    # ── Persistence ───────────────────────────────────────────────────────

    def save(self, path: str | Path, mlflow_run: bool = True) -> str:
        if self._net is None:
            raise RuntimeError("No model to save")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint = {
            "state_dict": self._net.state_dict(),
            "hparams": self.hp,
            "input_size": self._input_size,
            "train_metrics": self._train_metrics,
        }
        torch.save(checkpoint, str(path))
        logger.info("LSTM model saved to %s", path)

        if mlflow_run:
            try:
                import mlflow
                mlflow.log_artifact(str(path), "lstm_sequence")
                mlflow.log_metrics(self._train_metrics)
            except Exception:
                logger.warning("MLflow logging failed", exc_info=True)

        return str(path)

    def load(self, path: str | Path) -> None:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"No checkpoint at {path}")
        checkpoint = torch.load(str(path), map_location=DEVICE, weights_only=False)
        self.hp = checkpoint["hparams"]
        self._input_size = checkpoint["input_size"]
        self._train_metrics = checkpoint.get("train_metrics", {})

        self._net = _LSTMAttentionNet(
            input_size=self._input_size,
            hidden_size=self.hp["hidden_size"],
            num_layers=self.hp["num_layers"],
            num_heads=self.hp["num_heads"],
            dropout=self.hp["dropout"],
            fc_hidden=self.hp["fc_hidden"],
        ).to(DEVICE)
        self._net.load_state_dict(checkpoint["state_dict"])
        self._is_fitted = True
        logger.info("LSTM model loaded from %s", path)

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    @property
    def train_metrics(self) -> dict[str, float]:
        return dict(self._train_metrics)
