"""
Central asset specifications — pip size, digits, contract size, pip value per lot.

Single source of truth for SL/TP, sizing, and PnL across all services.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "assets.yaml"


@dataclass(frozen=True)
class AssetSpec:
    symbol: str
    pip_size: float          # price increment per pip
    digits: int              # decimal places for quotes
    contract_size: float     # units per 1.0 lot
    pip_value_per_lot: float # USD value of 1 pip per standard lot
    spread_limit_pips: float = 5.0
    min_sl_pips: float = 5.0
    min_tp_pips: float = 8.0
    min_atr: float = 0.001
    asset_class: str = "forex"


# Built-in defaults when assets.yaml has no per-symbol entry
_BUILTIN: dict[str, AssetSpec] = {
    "XAUUSD": AssetSpec("XAUUSD", 0.01, 2, 100.0, 1.0, 3.0, 10.0, 15.0, 3.0, "commodity"),
    "GOLD": AssetSpec("XAUUSD", 0.01, 2, 100.0, 1.0, 3.0, 10.0, 15.0, 3.0, "commodity"),
    "XAGUSD": AssetSpec("XAGUSD", 0.001, 3, 5000.0, 5.0, 5.0, 15.0, 25.0, 0.05, "commodity"),
    "USOIL": AssetSpec("USOIL", 0.01, 2, 1000.0, 10.0, 5.0, 15.0, 25.0, 0.5, "commodity"),
    "UKOIL": AssetSpec("UKOIL", 0.01, 2, 1000.0, 10.0, 5.0, 15.0, 25.0, 0.5, "commodity"),
    "US30": AssetSpec("US30", 1.0, 1, 1.0, 1.0, 10.0, 20.0, 40.0, 50.0, "index"),
    "US500": AssetSpec("US500", 0.1, 1, 1.0, 1.0, 5.0, 10.0, 20.0, 5.0, "index"),
    "USTEC": AssetSpec("USTEC", 0.1, 1, 1.0, 1.0, 5.0, 10.0, 20.0, 5.0, "index"),
    "GER40": AssetSpec("GER40", 0.1, 1, 1.0, 1.0, 5.0, 10.0, 20.0, 5.0, "index"),
    "BTCUSD": AssetSpec("BTCUSD", 1.0, 1, 1.0, 1.0, 50.0, 100.0, 200.0, 100.0, "crypto"),
    "ETHUSD": AssetSpec("ETHUSD", 0.1, 1, 1.0, 1.0, 20.0, 50.0, 100.0, 10.0, "crypto"),
}


def _normalize_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "").replace(".", "")


def _infer_spec(symbol: str) -> AssetSpec:
    s = _normalize_symbol(symbol)
    if s in _BUILTIN:
        return _BUILTIN[s]
    if s.startswith("XAU") or s == "GOLD":
        return _BUILTIN["XAUUSD"]
    if s.startswith("XAG"):
        return _BUILTIN["XAGUSD"]
    if "JPY" in s:
        return AssetSpec(s, 0.01, 3, 100_000.0, 6.5, 3.0, 8.0, 12.0, 0.10, "forex")
    if any(c in s for c in ("BTC", "ETH", "SOL", "XRP")):
        return _BUILTIN["BTCUSD"]
    if any(idx in s for idx in ("US30", "US500", "USTEC", "GER40", "NAS", "DAX")):
        return _BUILTIN["US30"]
    # Standard forex
    return AssetSpec(s, 0.0001, 5, 100_000.0, 10.0, 3.0, 8.0, 12.0, 0.001, "forex")


@lru_cache(maxsize=1)
def _load_yaml_specs() -> dict[str, AssetSpec]:
    """Load per-symbol overrides from assets.yaml specs section."""
    out: dict[str, AssetSpec] = {}
    if not _CONFIG_PATH.exists():
        return out
    try:
        with open(_CONFIG_PATH) as f:
            raw = yaml.safe_load(f) or {}
        specs = raw.get("specs", {})
        if isinstance(specs, dict):
            for sym, cfg in specs.items():
                if not isinstance(cfg, dict):
                    continue
                base = _infer_spec(sym)
                out[_normalize_symbol(sym)] = AssetSpec(
                    symbol=_normalize_symbol(sym),
                    pip_size=float(cfg.get("pip_size", base.pip_size)),
                    digits=int(cfg.get("digits", base.digits)),
                    contract_size=float(cfg.get("contract_size", base.contract_size)),
                    pip_value_per_lot=float(cfg.get("pip_value_per_lot", base.pip_value_per_lot)),
                    spread_limit_pips=float(cfg.get("spread_limit_pips", base.spread_limit_pips)),
                    min_sl_pips=float(cfg.get("min_sl_pips", base.min_sl_pips)),
                    min_tp_pips=float(cfg.get("min_tp_pips", base.min_tp_pips)),
                    min_atr=float(cfg.get("min_atr", base.min_atr)),
                    asset_class=str(cfg.get("asset_class", base.asset_class)),
                )
    except Exception:
        pass
    return out


def get_asset_spec(symbol: str) -> AssetSpec:
    s = _normalize_symbol(symbol)
    yaml_specs = _load_yaml_specs()
    if s in yaml_specs:
        return yaml_specs[s]
    return _infer_spec(s)


def get_pip_size(symbol: str) -> float:
    return get_asset_spec(symbol).pip_size


def get_pip_value(symbol: str) -> float:
    """Pip size in price units (same as pip_size for distance calculations)."""
    return get_asset_spec(symbol).pip_size


def get_digits(symbol: str) -> int:
    return get_asset_spec(symbol).digits


def get_contract_size(symbol: str) -> float:
    return get_asset_spec(symbol).contract_size


def get_pip_value_per_lot(symbol: str) -> float:
    return get_asset_spec(symbol).pip_value_per_lot


def price_to_pips(symbol: str, price_distance: float) -> float:
    """Convert a price delta to pips, preserving sign (losses stay negative)."""
    pip = get_pip_size(symbol)
    if pip <= 0:
        return 0.0
    return price_distance / pip


def pips_to_price(symbol: str, pips: float) -> float:
    return pips * get_pip_size(symbol)


def round_price(symbol: str, price: float) -> float:
    return round(price, get_digits(symbol))
