"""
Nexus Trader — YAML configuration loader.

Features:
  * Load ``settings.yaml``, ``sessions.yaml``, ``risk.yaml``,
    ``agents.yaml``, ``assets.yaml`` from the project ``config/`` dir.
  * Environment-variable overrides (``NEXUS_<SECTION>__<KEY>``).
  * Optional hot-reload via background file-watcher.
  * Pydantic validation of every loaded config section.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Callable

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = structlog.get_logger(__name__)

_CONFIG_DIR = Path(
    os.getenv("NEXUS_CONFIG_DIR", str(Path(__file__).resolve().parent.parent / "config"))
)


# ── Pydantic config schemas ─────────────────────────────────────────────────

class SettingsSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    trading_mode: str = "paper"
    redis_url: str = "redis://localhost:6379/0"
    duckdb_path: str = "data/nexus.duckdb"
    mlflow_tracking_uri: str = "http://localhost:5000"
    log_level: str = "INFO"
    heartbeat_interval: int = 5
    default_symbol: str = "XAUUSD"
    default_timeframe: str = "M15"
    auto_trade_enabled: bool = False


class SessionConfigSchema(BaseModel):
    name: str
    active_agents: list[str] = Field(default_factory=list)
    max_trades: int = 10
    eligible_pairs: list[str] = Field(default_factory=list)
    volatility_mode: str = "normal"
    size_multiplier: float = 1.0


class RiskRulesSchema(BaseModel):
    max_risk_per_trade_pct: float = 2.0
    max_daily_loss_pct: float = 5.0
    max_weekly_drawdown_pct: float = 8.0
    max_open_positions: int = 3
    max_correlation_exposure: float = 0.70
    min_rr_ratio: float = 1.5
    max_spread_pips: float = 3.0
    min_conviction: float = 0.60
    position_size_cap_lots: float = 1.0
    trailing_stop_pips: int = 15
    break_even_trigger_pips: int = 20
    daily_trade_limit: int = 10
    cooldown_after_loss_sec: int = 300


class AgentConfigSchema(BaseModel):
    id: str
    name: str
    enabled: bool = True
    model: str = ""
    temperature: float = 0.2
    prompt_template: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class AssetConfigSchema(BaseModel):
    symbol: str
    pip_value: float = 0.0001
    spread_limit: float = 3.0
    lot_step: float = 0.01
    sessions: list[str] = Field(default_factory=list)


# ── Loader ───────────────────────────────────────────────────────────────────

class ConfigLoader:
    """Load, validate, cache and optionally hot-reload YAML configs."""

    def __init__(self, config_dir: str | Path | None = None) -> None:
        self._dir = Path(config_dir) if config_dir else _CONFIG_DIR
        self._cache: dict[str, Any] = {}
        self._watch_task: asyncio.Task[None] | None = None
        self._callbacks: list[Callable[[str], Any]] = []
        self._mtimes: dict[str, float] = {}

    # ── Public loaders ───────────────────────────────────────────────────

    def load_settings(self) -> dict[str, Any]:
        raw = self._load_yaml("settings.yaml")
        # Legacy / launcher alias (run.py); ``NEXUS_SETTINGS__TRADING_MODE`` still wins below.
        legacy_mode = os.getenv("NEXUS_TRADING_MODE", "").strip().lower()
        if legacy_mode in ("live", "paper"):
            raw["trading_mode"] = legacy_mode
        raw = self._apply_env_overrides("settings", raw)
        validated = SettingsSchema(**raw)
        self._cache["settings"] = validated.model_dump()
        return self._cache["settings"]

    def load_sessions(self) -> dict[str, Any]:
        raw = self._load_yaml("sessions.yaml")
        validated: dict[str, Any] = {}
        sessions_list = raw.get("sessions", raw) if isinstance(raw, dict) else raw
        if isinstance(sessions_list, list):
            for s in sessions_list:
                sch = SessionConfigSchema(**s)
                validated[sch.name] = sch.model_dump()
        elif isinstance(sessions_list, dict):
            for name, cfg in sessions_list.items():
                if isinstance(cfg, dict):
                    cfg.setdefault("name", name)
                    sch = SessionConfigSchema(**cfg)
                    validated[name] = sch.model_dump()
        self._cache["sessions"] = validated
        return validated

    def load_risk_rules(self) -> dict[str, Any]:
        raw = self._load_yaml("risk.yaml")
        raw = self._apply_env_overrides("risk", raw)
        validated = RiskRulesSchema(**raw)
        self._cache["risk"] = validated.model_dump()
        return self._cache["risk"]

    def load_agents(self) -> dict[str, Any]:
        raw = self._load_yaml("agents.yaml")
        validated: dict[str, Any] = {}
        agents_list = raw.get("agents", raw) if isinstance(raw, dict) else raw
        if isinstance(agents_list, list):
            for a in agents_list:
                sch = AgentConfigSchema(**a)
                validated[sch.id] = sch.model_dump()
        elif isinstance(agents_list, dict):
            for aid, cfg in agents_list.items():
                if isinstance(cfg, dict):
                    cfg.setdefault("id", aid)
                    sch = AgentConfigSchema(**cfg)
                    validated[aid] = sch.model_dump()
        self._cache["agents"] = validated
        return validated

    def load_assets(self) -> dict[str, Any]:
        raw = self._load_yaml("assets.yaml")
        validated: dict[str, Any] = {}
        assets_list = raw.get("assets", raw) if isinstance(raw, dict) else raw
        if isinstance(assets_list, list):
            for a in assets_list:
                sch = AssetConfigSchema(**a)
                validated[sch.symbol] = sch.model_dump()
        elif isinstance(assets_list, dict):
            for sym, cfg in assets_list.items():
                if isinstance(cfg, dict):
                    cfg.setdefault("symbol", sym)
                    sch = AssetConfigSchema(**cfg)
                    validated[sym] = sch.model_dump()
        self._cache["assets"] = validated
        return validated

    def load_all(self) -> dict[str, Any]:
        """Load every config file. Missing files are silently skipped."""
        result: dict[str, Any] = {}
        for name, loader in [
            ("settings", self.load_settings),
            ("sessions", self.load_sessions),
            ("risk", self.load_risk_rules),
            ("agents", self.load_agents),
            ("assets", self.load_assets),
        ]:
            try:
                result[name] = loader()
            except FileNotFoundError:
                logger.warning("config.file_not_found", file=name)
            except (ValidationError, yaml.YAMLError) as exc:
                logger.warning("config.validation_error", file=name, error=str(exc))
        return result

    # ── Cached access ────────────────────────────────────────────────────

    def get(self, section: str) -> Any | None:
        return self._cache.get(section)

    # ── Hot-reload ───────────────────────────────────────────────────────

    def on_reload(self, callback: Callable[[str], Any]) -> None:
        self._callbacks.append(callback)

    async def start_watching(self, interval_sec: float = 2.0) -> None:
        self._snapshot_mtimes()
        self._watch_task = asyncio.create_task(self._watch_loop(interval_sec))

    async def stop_watching(self) -> None:
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass

    async def _watch_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            for path, old_mtime in list(self._mtimes.items()):
                try:
                    current = Path(path).stat().st_mtime
                except FileNotFoundError:
                    continue
                if current != old_mtime:
                    self._mtimes[path] = current
                    section = Path(path).stem
                    logger.info("config.file_changed", file=section)
                    try:
                        self.load_all()
                    except Exception:
                        logger.exception("config.reload_error", file=section)
                        continue
                    for cb in self._callbacks:
                        try:
                            result = cb(section)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception:
                            logger.exception("config.callback_error")

    # ── Internals ────────────────────────────────────────────────────────

    def _load_yaml(self, filename: str) -> dict[str, Any]:
        path = self._dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _apply_env_overrides(section: str, data: dict[str, Any]) -> dict[str, Any]:
        """Override values via ``NEXUS_<SECTION>__<KEY>`` env vars."""
        prefix = f"NEXUS_{section.upper()}__"
        for key, value in os.environ.items():
            if key.startswith(prefix):
                field = key[len(prefix):].lower()
                data[field] = value
        return data

    def _snapshot_mtimes(self) -> None:
        if not self._dir.exists():
            return
        for p in self._dir.glob("*.yaml"):
            self._mtimes[str(p)] = p.stat().st_mtime
