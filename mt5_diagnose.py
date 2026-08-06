#!/usr/bin/env python3
"""
MT5 Diagnostic — run this to check what symbols are available and fix aliases.

Usage:
    python mt5_diagnose.py

What it does:
  1. Loads credentials from .env
  2. Connects to MetaTrader 5 terminal
  3. Lists all symbols that contain XAU or BTC in their name
  4. Tries symbol_select on the configured aliases
  5. Prints the EXACT MT5_SYMBOL_ALIASES line you need in .env
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_env() -> dict[str, str]:
    env_path = ROOT / ".env"
    try:
        from dotenv import dotenv_values
        return {str(k): str(v) for k, v in dotenv_values(env_path).items() if k and v}
    except ImportError:
        print("[ERROR] python-dotenv not installed. Run: pip install python-dotenv")
        sys.exit(1)


def main() -> None:
    print("\n=== Nexus MT5 Diagnostic ===\n")

    env = load_env()
    login = int(env.get("MT5_LOGIN", "0"))
    password = env.get("MT5_PASSWORD", "")
    server = env.get("MT5_SERVER", "")
    path = env.get("MT5_PATH", "")

    print(f"  Login:  {login}")
    print(f"  Server: {server}")
    print(f"  Path:   {path or '(default)'}\n")

    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("[ERROR] MetaTrader5 package not installed.")
        print("        This script must run on Windows with MT5 installed.")
        sys.exit(1)

    # ── Connect ──────────────────────────────────────────────────────────────
    kwargs: dict = {"login": login, "password": password, "server": server}
    if path:
        kwargs["path"] = path

    print("Connecting to MT5 terminal...")
    if not mt5.initialize(**kwargs):
        code, msg = mt5.last_error()
        print(f"[ERROR] MT5 initialize failed: [{code}] {msg}")
        print("\nMake sure MetaTrader 5 is open and you are logged in.")
        sys.exit(1)

    print("[OK] Connected to MT5\n")

    try:
        # ── Account info ─────────────────────────────────────────────────────
        info = mt5.account_info()
        if info:
            print(f"Account: #{info.login}  Balance: {info.balance:.2f} {info.currency}  "
                  f"Server: {info.server}\n")

        # ── Search for XAU and BTC symbols ───────────────────────────────────
        all_syms = mt5.symbols_get()
        if all_syms is None:
            print("[WARN] mt5.symbols_get() returned None")
            all_syms = []

        xau_syms = [s.name for s in all_syms if "XAU" in s.name.upper()]
        btc_syms = [s.name for s in all_syms if "BTC" in s.name.upper()]

        print("──── XAU symbols on this broker server ────")
        if xau_syms:
            for s in sorted(xau_syms):
                print(f"  {s}")
        else:
            print("  (none found)")

        print("\n──── BTC symbols on this broker server ────")
        if btc_syms:
            for s in sorted(btc_syms):
                print(f"  {s}")
        else:
            print("  (none found)")

        # ── Try symbol_select on current aliases ──────────────────────────────
        aliases_raw = env.get("MT5_SYMBOL_ALIASES", "")
        aliases: dict[str, str] = {}
        for part in aliases_raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                aliases[k.strip().upper()] = v.strip()

        print(f"\n──── Testing current aliases ({aliases_raw or 'none'}) ────")
        for logical, broker_sym in (aliases or {"XAUUSD": "XAUUSD", "BTCUSD": "BTCUSD"}).items():
            ok = mt5.symbol_select(broker_sym, True)
            status = "[OK]   " if ok else "[FAIL]"
            tick = mt5.symbol_info_tick(broker_sym) if ok else None
            price = f"  bid={tick.bid:.2f}" if tick else ""
            print(f"  {status} {logical} → {broker_sym}{price}")

        # ── Suggest best aliases ──────────────────────────────────────────────
        print("\n──── Recommended .env line ────")

        def best_match(candidates: list[str], keyword: str) -> str:
            if not candidates:
                return keyword
            # prefer shortest match (XAUUSDm < XAUUSDm_ecn etc.)
            return sorted(candidates, key=len)[0]

        xau_best = best_match(xau_syms, "XAUUSD")
        btc_best = best_match(btc_syms, "BTCUSD")

        alias_line = f"MT5_SYMBOL_ALIASES=XAUUSD={xau_best};BTCUSD={btc_best}"
        print(f"\n  {alias_line}\n")

        current = env.get("MT5_SYMBOL_ALIASES", "")
        if current == f"XAUUSD={xau_best};BTCUSD={btc_best}":
            print("  ✓ Your .env already has the correct aliases!")
        else:
            print("  ← Copy the line above into your .env file.")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
