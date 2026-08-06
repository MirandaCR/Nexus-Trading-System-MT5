"""
Auto-Retrain Scheduler — Periodically retrains ML/DL/RL models.

Addresses the critical gap: models were static after initial training.
For scalping, retrains every 4 hours; for swing, every 24 hours.
Supports warm-start for XGBoost and fine-tuning for Transformer/PPO.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


class AutoRetrainScheduler:
    """Background scheduler that triggers model retraining at configured intervals."""

    def __init__(
        self,
        retrain_interval_sec: int = 14400,  # 4 hours default
        retrain_callback: Callable[[], Awaitable[None]] | None = None,
        on_retrain_complete: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.interval = retrain_interval_sec
        self._callback = retrain_callback
        self._on_complete = on_retrain_complete
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_retrain: float = 0.0
        self._retrain_count: int = 0
        self._last_metrics: dict[str, Any] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_retrain_time(self) -> str:
        if self._last_retrain == 0:
            return "Never"
        return datetime.fromtimestamp(self._last_retrain, tz=timezone.utc).isoformat()

    @property
    def next_retrain_in(self) -> float:
        """Seconds until next scheduled retrain."""
        if not self._running or self._last_retrain == 0:
            return 0.0
        elapsed = time.time() - self._last_retrain
        return max(0.0, self.interval - elapsed)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "interval_sec": self.interval,
            "interval_human": f"{self.interval // 3600}h {(self.interval % 3600) // 60}m",
            "retrain_count": self._retrain_count,
            "last_retrain": self.last_retrain_time,
            "next_retrain_in_sec": round(self.next_retrain_in),
            "last_metrics": self._last_metrics,
        }

    def start(self) -> None:
        """Start the background retraining loop."""
        if self._running:
            logger.warning("AutoRetrainScheduler already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "AutoRetrainScheduler started: interval=%ds (%dh)",
            self.interval, self.interval // 3600,
        )

    def stop(self) -> None:
        """Stop the background retraining loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("AutoRetrainScheduler stopped")

    def update_interval(self, new_interval_sec: int) -> None:
        """Update the retraining interval (takes effect next cycle)."""
        self.interval = new_interval_sec
        logger.info("Retrain interval updated to %ds", new_interval_sec)

    def force_retrain(self) -> None:
        """Force an immediate retrain on the next cycle."""
        self._last_retrain = 0.0
        logger.info("Force retrain scheduled")

    async def _loop(self) -> None:
        """Main retraining loop."""
        # Initial delay: let the system stabilize before first retrain
        await asyncio.sleep(60)

        while self._running:
            try:
                now = time.time()
                is_initial = self._last_retrain == 0
                # On the very first cycle there is no previous retrain to measure
                # from, so report 0 instead of (now - 0) which is the epoch
                # timestamp (~1.78e9s, the misleading "elapsed=1780592207s").
                elapsed = 0.0 if is_initial else now - self._last_retrain

                if is_initial or elapsed >= self.interval:
                    logger.info(
                        "Auto-retrain #%d triggered (%s)",
                        self._retrain_count + 1,
                        "initial startup" if is_initial else f"elapsed={int(elapsed)}s",
                    )

                    t0 = time.time()
                    if self._callback:
                        await self._callback()

                    elapsed_train = time.time() - t0
                    self._last_retrain = time.time()
                    self._retrain_count += 1
                    self._last_metrics = {
                        "retrain_number": self._retrain_count,
                        "training_time_sec": round(elapsed_train, 1),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

                    if self._on_complete:
                        self._on_complete(self._last_metrics)

                    logger.info(
                        "Auto-retrain #%d complete in %.1fs",
                        self._retrain_count, elapsed_train,
                    )

                # Sleep in chunks so we can stop quickly
                sleep_time = min(30, self.interval // 4)
                await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Auto-retrain loop error", exc_info=True)
                await asyncio.sleep(60)  # back off on error

        logger.info("Auto-retrain loop exited")
