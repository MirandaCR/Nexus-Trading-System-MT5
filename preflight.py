#!/usr/bin/env python3
"""
Nexus Trader — Pre-flight checklist before run.py.

Validates MT5, Redis, .env, symbol aliases, and optional running services
so you know the stack is ready for real-data trading.

Usage:
    python preflight.py              # full check (MT5 + Redis + .env)
    python preflight.py --quick      # skip MT5 terminal test
    python run.py --preflight        # same as above, then exit
    python run.py --skip-preflight   # start services without this check
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

# Service ports (aligned with run.py / shared.constants)
PORTS = {
    "nexus-data": 9001,
    "nexus-features": 9002,
    "nexus-ml": 9003,
    "nexus-agents": 9004,
    "nexus-risk": 9006,
    "nexus-executor": 9007,
    "nexus-api": 9000,
}


@dataclass
class Check:
    name: str
    status: str  # PASS | WARN | FAIL
    detail: str = ""
    fix: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "", fix: str = "") -> None:
        self.checks.append(Check(name, status, detail, fix))

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == "FAIL"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == "WARN"]

    @property
    def ok(self) -> bool:
        return len(self.failed) == 0


def _load_env() -> dict[str, str]:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return {}
    try:
        from dotenv import dotenv_values
        return {str(k): str(v) for k, v in dotenv_values(env_path).items() if k and v}
    except ImportError:
        return {}


def _parse_aliases(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            key = k.strip().upper().replace("/", "")
            val = v.strip()
            if key and val:
                out[key] = val
    return out


def _check_redis(url: str, report: Report) -> None:
    host, port = "localhost", 6379
    if url.startswith("redis://"):
        rest = url.replace("redis://", "").split("/")[0]
        if ":" in rest:
            host, port_s = rest.split(":", 1)
            port = int(port_s)
        else:
            host = rest or "localhost"

    try:
        import redis
        r = redis.from_url(url, socket_connect_timeout=3)
        r.ping()
        report.add("Redis", "PASS", f"PING OK @ {url}")
        return
    except ImportError:
        pass
    except Exception as exc:
        report.add(
            "Redis", "FAIL",
            f"No responde: {exc}",
            "Inicia Redis: docker run -d -p 6379:6379 redis:7-alpine",
        )
        return

    # TCP fallback
    try:
        with socket.create_connection((host, port), timeout=3):
            report.add("Redis", "PASS", f"TCP OK @ {host}:{port} (sin paquete redis)")
    except OSError as exc:
        report.add(
            "Redis", "FAIL",
            f"No alcanzable: {exc}",
            "Inicia Redis antes de run.py",
        )


def _check_env_file(report: Report) -> dict[str, str]:
    env_path = ROOT / ".env"
    if not env_path.exists():
        report.add(".env", "FAIL", "Archivo no encontrado", "cp .env.example .env")
        return {}
    report.add(".env", "PASS", str(env_path))
    return _load_env()


def _check_env_vars(env: dict[str, str], report: Report) -> None:
    required = ["MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "STREAM_SYMBOLS"]
    missing = [k for k in required if not env.get(k)]
    if missing:
        report.add(
            "Credenciales MT5", "FAIL",
            f"Faltan: {', '.join(missing)}",
            "Completa MT5_LOGIN, MT5_PASSWORD, MT5_SERVER en .env",
        )
    else:
        report.add("Credenciales MT5", "PASS", f"Login {env.get('MT5_LOGIN')} @ {env.get('MT5_SERVER')}")

    if env.get("NEXUS_DATA_SYNTHETIC_FALLBACK", "0").lower() in ("1", "true", "yes"):
        report.add(
            "Data sintética", "WARN",
            "NEXUS_DATA_SYNTHETIC_FALLBACK=1 — puede usar datos falsos si MT5 falla",
            "Pon NEXUS_DATA_SYNTHETIC_FALLBACK=0 para solo data real MT5",
        )
    else:
        report.add("Data sintética", "PASS", "Desactivada — solo MT5 real")

    if env.get("NEXUS_AUTONOMOUS_ML", "true").lower() in ("1", "true", "yes"):
        models_dir = ROOT / "models_store"
        has_models = False
        if models_dir.exists():
            exts = (".json", ".pkl", ".pt", ".joblib", ".txt")
            has_models = any(p.suffix in exts for p in models_dir.rglob("*") if p.is_file())
        if not has_models:
            report.add(
                "ML autónomo", "WARN",
                "NEXUS_AUTONOMOUS_ML=true pero no hay modelos en models_store/",
                "Pon NEXUS_AUTONOMOUS_ML=false hasta entrenar, o entrena vía POST /api/traders/train",
            )
        else:
            report.add("ML autónomo", "PASS", "Activado con modelos en models_store/")
    else:
        report.add("ML autónomo", "PASS", "Desactivado (recomendado hasta entrenar)")

    conviction = int(env.get("NEXUS_MIN_VERDICT_CONVICTION", "70") or "70")
    if conviction < 60:
        report.add(
            "Convicción mínima", "WARN",
            f"NEXUS_MIN_VERDICT_CONVICTION={conviction} — muy permisivo para scalping",
            "Usa 65–70 al validar; baja solo si hay muy pocas señales",
        )
    else:
        report.add("Convicción mínima", "PASS", str(conviction))

    if env.get("NEXUS_ALLOW_OFF_HOURS", "false").lower() in ("1", "true", "yes"):
        report.add(
            "Off-hours", "WARN",
            "NEXUS_ALLOW_OFF_HOURS=true — opera fuera de sesiones líquidas",
            "false para scalping XAUUSD",
        )
    else:
        report.add("Off-hours", "PASS", "Solo sesiones activas")

    tf = env.get("NEXUS_OPERATIONAL_TF", "M5")
    report.add("Timeframe operativo", "PASS" if tf == "M5" else "WARN", tf)


def _check_mt5(env: dict[str, str], report: Report) -> None:
    if platform.system() != "Windows":
        report.add(
            "MT5 terminal", "WARN",
            "Pre-flight MT5 solo en Windows — omite en este OS",
        )
        return

    try:
        import MetaTrader5 as mt5
    except ImportError:
        report.add(
            "MT5 package", "FAIL",
            "pip install MetaTrader5",
            "Requerido en Windows con terminal MT5",
        )
        return

    login = int(env.get("MT5_LOGIN", "0") or "0")
    password = env.get("MT5_PASSWORD", "")
    server = env.get("MT5_SERVER", "")
    path = env.get("MT5_PATH", "")

    kwargs: dict[str, Any] = {"login": login, "password": password, "server": server}
    if path:
        kwargs["path"] = path

    if not mt5.initialize(**kwargs):
        code, msg = mt5.last_error()
        report.add(
            "MT5 conexión", "FAIL",
            f"[{code}] {msg}",
            "Abre MetaTrader 5, inicia sesión y activa AutoTrading",
        )
        return

    try:
        info = mt5.account_info()
        if info:
            report.add(
                "MT5 conexión", "PASS",
                f"#{info.login} balance={info.balance:.2f} {info.currency}",
            )
        else:
            report.add("MT5 conexión", "WARN", "Conectado pero sin account_info")

        # Terminal AutoTrading
        ti = mt5.terminal_info()
        if ti and not ti.trade_allowed:
            report.add(
                "AutoTrading", "WARN",
                "AutoTrading desactivado en el terminal",
                "Activa el botón 'Algo Trading' en MT5",
            )
        else:
            report.add("AutoTrading", "PASS", "Permitido en terminal")

        aliases = _parse_aliases(env.get("MT5_SYMBOL_ALIASES", ""))
        stream = [
            s.strip().upper().replace("/", "")
            for s in env.get("STREAM_SYMBOLS", "XAUUSD").split(",")
            if s.strip()
        ]

        all_syms = mt5.symbols_get() or []
        names = [s.name for s in all_syms]

        for logical in stream:
            broker_sym = aliases.get(logical, logical)
            ok = mt5.symbol_select(broker_sym, True)
            tick = mt5.symbol_info_tick(broker_sym) if ok else None
            if ok and tick and tick.bid > 0:
                report.add(
                    f"Símbolo {logical}", "PASS",
                    f"→ {broker_sym} bid={tick.bid:.2f}",
                )
            elif ok:
                report.add(
                    f"Símbolo {logical}", "WARN",
                    f"→ {broker_sym} seleccionado pero sin tick",
                    "Espera mercado abierto o revisa Observación del Mercado",
                )
            else:
                # Sugerir mejor match
                keyword = logical.replace("USD", "").replace("/", "")[:3]
                candidates = [n for n in names if keyword in n.upper() or logical[:3] in n.upper()]
                hint = ""
                if logical.startswith("XAU"):
                    xau = sorted([n for n in names if "XAU" in n.upper() or n.upper() == "GOLD"], key=len)
                    if xau:
                        hint = f" Prueba: MT5_SYMBOL_ALIASES={logical}={xau[0]}"
                elif candidates:
                    hint = f" Candidatos: {', '.join(candidates[:5])}"
                report.add(
                    f"Símbolo {logical}", "FAIL",
                    f"→ {broker_sym} no disponible{hint}",
                    f"Corrige MT5_SYMBOL_ALIASES en .env{hint}",
                )

    finally:
        mt5.shutdown()


def _check_models(report: Report) -> None:
    store = ROOT / "models_store"
    traders = store / "traders"
    if not store.exists():
        report.add(
            "Modelos ML", "WARN",
            "models_store/ no existe — ensemble usará rule-based",
            "Acumula data MT5 24–48h y entrena: POST /api/traders/train",
        )
        return

    artifacts = list(store.glob("**/*"))
    model_files = [p for p in artifacts if p.suffix in (".json", ".pkl", ".pt", ".joblib", ".txt")]
    if not model_files:
        report.add("Modelos ML", "WARN", "Sin archivos de modelo entrenados")
    else:
        report.add("Modelos ML", "PASS", f"{len(model_files)} archivo(s) en models_store/")


def _check_running_services(report: Report) -> None:
    """Optional: report if services already up."""
    up: list[str] = []
    for name, port in PORTS.items():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                up.append(f"{name}:{port}")
        except OSError:
            pass

    if not up:
        report.add("Servicios", "PASS", "Ninguno corriendo — listo para python run.py")
        return

    report.add("Servicios", "WARN", f"Ya activos: {', '.join(up)}", "Para reiniciar limpio, detén esos procesos")

    # If nexus-data is up, probe health
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORTS['nexus-data']}/health", timeout=3) as resp:
            data = json.loads(resp.read().decode())
            mt5_ok = data.get("mt5", False)
            mode = data.get("data_mode", "?")
            if mt5_ok:
                report.add("nexus-data health", "PASS", f"data_mode={mode}")
            else:
                report.add(
                    "nexus-data health", "WARN",
                    f"mt5=false data_mode={mode}",
                    "Revisa MT5 y reinicia nexus-data",
                )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        pass


def run_preflight(*, quick: bool = False, check_services: bool = True) -> Report:
    report = Report()
    env = _check_env_file(report)
    if env:
        _check_env_vars(env, report)
    _check_redis(env.get("REDIS_URL", "redis://localhost:6379"), report)
    _check_models(report)
    if not quick and env:
        _check_mt5(env, report)
    elif quick:
        report.add("MT5 terminal", "PASS", "Omitido (--quick)")
    if check_services:
        _check_running_services(report)
    return report


def _print_report(report: Report) -> None:
    icons = {"PASS": "\033[32m✓\033[0m", "WARN": "\033[33m!\033[0m", "FAIL": "\033[31m✗\033[0m"}
    print("\n\033[1m═══ Nexus Trader Pre-flight ═══\033[0m\n")
    for c in report.checks:
        icon = icons.get(c.status, "?")
        print(f"  {icon}  [{c.status:4}] {c.name}")
        if c.detail:
            print(f"         {c.detail}")
        if c.fix and c.status != "PASS":
            print(f"         \033[36m→ {c.fix}\033[0m")
    print()
    n_fail = len(report.failed)
    n_warn = len(report.warnings)
    if report.ok:
        print("\033[32m\033[1mLISTO\033[0m — Puedes ejecutar: \033[1mpython run.py\033[0m")
        if n_warn:
            print(f"  ({n_warn} advertencia(s) — revisa antes de operar en vivo)")
    else:
        print(f"\033[31m\033[1mBLOQUEADO\033[0m — {n_fail} error(es) crítico(s). Corrige antes de run.py.")
    print()


def main() -> int:
    if platform.system() == "Windows":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Nexus Trader pre-flight checks")
    parser.add_argument("--quick", action="store_true", help="Skip MT5 terminal connection test")
    parser.add_argument("--no-services", action="store_true", help="Do not check if services already running")
    args = parser.parse_args()

    os.chdir(ROOT)
    report = run_preflight(quick=args.quick, check_services=not args.no_services)
    _print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
