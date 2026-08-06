"""
Nexus Risk — Anti-tilt emotional trading detector.

Monitors consecutive losses, trade frequency, and size escalation patterns
to detect emotional/revenge trading and enforce cooldown periods.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum, unique
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# ── Defaults from risk_rules.yaml ────────────────────────────────────────────

_DEFAULT_WARNING_THRESHOLD = 3
_DEFAULT_PAUSE_THRESHOLD = 5
_DEFAULT_COOLDOWN_MINUTES = 30


@unique
class TiltStatus(StrEnum):
    NORMAL  = "NORMAL"
    WARNING = "WARNING"
    TILT    = "TILT"


@dataclass
class TiltState:
    status: TiltStatus = TiltStatus.NORMAL
    consecutive_losses: int = 0
    cooldown_until: float = 0.0
    last_update: float = field(default_factory=time.monotonic)
    session_losses: int = 0
    session_wins: int = 0


class AntiTilt:
    """Detects emotional trading patterns and enforces cooldowns."""

    def __init__(
        self,
        warning_threshold: int = _DEFAULT_WARNING_THRESHOLD,
        pause_threshold: int = _DEFAULT_PAUSE_THRESHOLD,
        cooldown_minutes: float = _DEFAULT_COOLDOWN_MINUTES,
    ) -> None:
        self._warning_threshold = warning_threshold
        self._pause_threshold = pause_threshold
        self._cooldown_minutes = cooldown_minutes
        self._state = TiltState()
        self._recent_trade_times: list[float] = []
        self._recent_trade_sizes: list[float] = []

    # ── Update on Trade Result ────────────────────────────────────────────

    def update(self, trade_result: dict[str, Any]) -> TiltStatus:
        pnl = trade_result.get("pnl_usd", trade_result.get("pnl_pips", 0.0))
        size = trade_result.get("size", 0.01)
        now = time.monotonic()

        self._recent_trade_times.append(now)
        self._recent_trade_sizes.append(size)

        if len(self._recent_trade_times) > 50:
            self._recent_trade_times = self._recent_trade_times[-50:]
        if len(self._recent_trade_sizes) > 50:
            self._recent_trade_sizes = self._recent_trade_sizes[-50:]

        if pnl < 0:
            self._state.consecutive_losses += 1
            self._state.session_losses += 1
        else:
            self._state.consecutive_losses = 0
            self._state.session_wins += 1

        prev = self._state.status
        if self._state.consecutive_losses >= self._pause_threshold:
            self._state.status = TiltStatus.TILT
            self._state.cooldown_until = now + (self._cooldown_minutes * 60)
            log.warning(
                "anti_tilt.tilt_triggered",
                consecutive_losses=self._state.consecutive_losses,
                cooldown_minutes=self._cooldown_minutes,
            )
        elif self._state.consecutive_losses >= self._warning_threshold:
            self._state.status = TiltStatus.WARNING
            log.info(
                "anti_tilt.warning",
                consecutive_losses=self._state.consecutive_losses,
            )
        elif self._state.status == TiltStatus.TILT and now < self._state.cooldown_until:
            pass  # Stay in TILT until cooldown expires
        elif self._state.status == TiltStatus.TILT and now >= self._state.cooldown_until:
            self._state.status = TiltStatus.NORMAL
            self._state.consecutive_losses = 0
            log.info("anti_tilt.cooldown_expired")
        else:
            self._state.status = TiltStatus.NORMAL

        self._state.last_update = now

        if self._state.status != prev:
            log.info(
                "anti_tilt.status_change",
                previous=prev,
                current=self._state.status,
            )

        return self._state.status

    # ── Status Queries ────────────────────────────────────────────────────

    def get_tilt_status(self) -> TiltStatus:
        now = time.monotonic()
        if (
            self._state.status == TiltStatus.TILT
            and now >= self._state.cooldown_until
            and self._state.cooldown_until > 0
        ):
            self._state.status = TiltStatus.NORMAL
            self._state.consecutive_losses = 0
            log.info("anti_tilt.cooldown_expired_on_check")
        return self._state.status

    def get_state_dict(self) -> dict[str, Any]:
        return {
            "status": self.get_tilt_status().value,
            "consecutive_losses": self._state.consecutive_losses,
            "session_losses": self._state.session_losses,
            "session_wins": self._state.session_wins,
            "cooldown_remaining_min": round(self.get_cooldown_remaining(), 1),
        }

    # ── Frequency Analysis ────────────────────────────────────────────────

    def check_frequency(self, recent_trades: list[dict[str, Any]] | None = None) -> bool:
        """Return True if trading frequency is abnormally high (>6 trades in 30 min)."""
        now = time.monotonic()
        window = 30 * 60  # 30 minutes

        if recent_trades:
            timestamps = []
            for t in recent_trades:
                ts = t.get("closed_at") or t.get("timestamp")
                if isinstance(ts, datetime):
                    timestamps.append(ts.timestamp())
                elif isinstance(ts, (int, float)):
                    timestamps.append(ts)
            cutoff = datetime.now(timezone.utc).timestamp() - window
            count = sum(1 for ts in timestamps if ts >= cutoff)
        else:
            cutoff = now - window
            count = sum(1 for t in self._recent_trade_times if t >= cutoff)

        is_abnormal = count > 6
        if is_abnormal:
            log.warning("anti_tilt.frequency_abnormal", trades_in_30min=count)
        return is_abnormal

    # ── Size Escalation Detection ─────────────────────────────────────────

    def check_size_escalation(self, recent_trades: list[dict[str, Any]] | None = None) -> bool:
        """Return True if sizes are increasing after consecutive losses."""
        if recent_trades:
            sizes = [t.get("size", 0.01) for t in recent_trades[-5:]]
        else:
            sizes = list(self._recent_trade_sizes[-5:])

        if len(sizes) < 3:
            return False

        increasing = all(sizes[i] < sizes[i + 1] for i in range(len(sizes) - 1))
        if increasing and self._state.consecutive_losses >= 2:
            log.warning(
                "anti_tilt.size_escalation",
                recent_sizes=sizes,
                consecutive_losses=self._state.consecutive_losses,
            )
            return True
        return False

    # ── Cooldown ──────────────────────────────────────────────────────────

    def get_cooldown_remaining(self) -> float:
        if self._state.status != TiltStatus.TILT or self._state.cooldown_until == 0:
            return 0.0
        remaining = self._state.cooldown_until - time.monotonic()
        return max(0.0, remaining / 60.0)

    # ── Redis Persistence ─────────────────────────────────────────────────

    _REDIS_KEY = "nexus:risk:tilt_state"

    async def restore_from_redis(self, redis_client: Any) -> None:
        """Restore consecutive_losses from Redis if same day (survives restarts)."""
        try:
            raw = await redis_client.get(self._REDIS_KEY)
            if not raw:
                return
            data = json.loads(raw)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if data.get("date") != today:
                return
            self._state.consecutive_losses = int(data.get("consecutive_losses", 0))
            self._state.session_losses = int(data.get("session_losses", 0))
            self._state.session_wins = int(data.get("session_wins", 0))
            # Re-evaluate status from restored count
            if self._state.consecutive_losses >= self._pause_threshold:
                self._state.status = TiltStatus.TILT
                # Set a 30-min cooldown from now since we can't restore monotonic time
                self._state.cooldown_until = time.monotonic() + (self._cooldown_minutes * 60)
            elif self._state.consecutive_losses >= self._warning_threshold:
                self._state.status = TiltStatus.WARNING
            log.info(
                "anti_tilt.restored_from_redis",
                consecutive_losses=self._state.consecutive_losses,
                status=self._state.status,
            )
        except Exception:
            log.debug("anti_tilt.restore_failed", exc_info=True)

    async def persist_to_redis(self, redis_client: Any) -> None:
        """Write current streak to Redis so it survives restarts."""
        try:
            await redis_client.set(
                self._REDIS_KEY,
                json.dumps({
                    "consecutive_losses": self._state.consecutive_losses,
                    "session_losses": self._state.session_losses,
                    "session_wins": self._state.session_wins,
                    "status": self._state.status.value,
                    "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                }),
                ex=86_400,
            )
        except Exception:
            log.debug("anti_tilt.persist_failed", exc_info=True)

    # ── Session Reset ─────────────────────────────────────────────────────

    def reset(self) -> None:
        log.info(
            "anti_tilt.session_reset",
            final_status=self._state.status,
            session_losses=self._state.session_losses,
            session_wins=self._state.session_wins,
        )
        self._state = TiltState()
        self._recent_trade_times.clear()
        self._recent_trade_sizes.clear()
