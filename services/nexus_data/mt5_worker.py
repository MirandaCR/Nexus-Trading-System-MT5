"""
Isolated MetaTrader 5 worker for nexus-data.

The MetaTrader5 Python extension can crash the host process on Windows
(STATUS_HEAP_CORRUPTION / 0xC0000374). nexus-data calls this helper in a
short-lived subprocess so a native crash kills only the worker, not the
FastAPI service.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any


def _fail(message: str, code: int = 1) -> None:
    print(json.dumps({"ok": False, "error": message}), flush=True)
    raise SystemExit(code)


def _timeframes(mt5) -> dict[str, int]:
    return {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }


def _initialise(mt5, payload: dict[str, Any]) -> None:
    # Credentials come from the environment, never the stdin payload. The
    # payload is the arg to asyncio's communicate(), whose internal `input`
    # local is dumped by rich tracebacks on any subprocess error — sending
    # the password there printed it in clear text in the logs. Env vars are
    # not shown in tracebacks. (Payload is still read as a fallback so an
    # older caller keeps working.)
    kwargs: dict[str, Any] = {
        "login": int(os.getenv("MT5_LOGIN") or payload.get("login") or 0),
        "password": os.getenv("MT5_PASSWORD") or payload.get("password") or "",
        "server": os.getenv("MT5_SERVER") or payload.get("server") or "",
    }
    path = payload.get("path") or ""
    if path:
        kwargs["path"] = path
    if not mt5.initialize(**kwargs):
        code, msg = mt5.last_error()
        _fail(f"MT5 initialize failed: [{code}] {msg}", 2)


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        _fail(f"bad worker payload: {exc}")

    try:
        import MetaTrader5 as mt5  # type: ignore[import-untyped]
    except Exception as exc:
        _fail(f"MetaTrader5 import failed: {exc}", 2)

    op = payload.get("op")
    symbol = str(payload.get("symbol") or "").upper().replace("/", "")
    broker_symbol = str(payload.get("broker_symbol") or symbol)

    try:
        _initialise(mt5, payload)
        if symbol and not mt5.symbol_select(broker_symbol, True):
            _fail(f"MT5 symbol_select failed for {broker_symbol!r}", 3)

        if op == "ping":
            info = mt5.account_info()
            out = {
                "ok": True,
                "connected": info is not None,
                "account": info._asdict() if info is not None else None,
            }
        elif op == "tick":
            tick = mt5.symbol_info_tick(broker_symbol)
            if tick is None:
                _fail(f"MT5 returned no tick for {broker_symbol!r}", 4)
            out = {
                "ok": True,
                "tick": {
                    "symbol": symbol,
                    "bid": float(tick.bid),
                    "ask": float(tick.ask),
                    "timestamp": datetime.fromtimestamp(tick.time, tz=timezone.utc).isoformat(),
                },
            }
        elif op == "ohlcv":
            tf = _timeframes(mt5).get(str(payload.get("timeframe") or "M15"))
            if tf is None:
                _fail(f"unsupported timeframe: {payload.get('timeframe')}", 5)
            count = int(payload.get("count") or 500)
            rates = mt5.copy_rates_from_pos(broker_symbol, tf, 0, count)
            if rates is None or len(rates) == 0:
                _fail(f"MT5 returned no OHLCV for {broker_symbol!r}", 6)
            out = {
                "ok": True,
                "bars": [
                    {
                        "symbol": symbol,
                        "timeframe": payload.get("timeframe") or "M15",
                        "open": float(r["open"]),
                        "high": float(r["high"]),
                        "low": float(r["low"]),
                        "close": float(r["close"]),
                        "volume": float(r["tick_volume"]),
                        "timestamp": datetime.fromtimestamp(r["time"], tz=timezone.utc).isoformat(),
                    }
                    for r in rates
                ],
            }
        elif op == "account":
            info = mt5.account_info()
            if info is None:
                _fail("MT5 account_info returned None", 7)
            out = {"ok": True, "account": info._asdict()}
        else:
            _fail(f"unsupported op: {op}", 8)
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass

    print(json.dumps(out, default=str), flush=True)


if __name__ == "__main__":
    main()
