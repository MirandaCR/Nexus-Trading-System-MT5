"""
MetaTrader 5 real-time data connector.

IMPORTANT (Windows): The MetaTrader5 C-extension uses COM/ATL under the
hood. COM requires that every call happens on the SAME OS thread that called
CoInitialize(). Using the default asyncio ThreadPoolExecutor (which rotates
worker threads) causes STATUS_HEAP_CORRUPTION (exit code 0xC0000374).

Fix: every MT5 call is dispatched through a single dedicated worker thread
(_MT5_EXECUTOR with max_workers=1). The thread is created once at import
time and is reused for every MT5 call for the lifetime of the process.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable

import structlog

from shared.models import Tick, OHLCV

log = structlog.get_logger("nexus_data.mt5")
ISOLATED_MT5 = os.getenv("NEXUS_MT5_ISOLATED", "1").lower() in ("1", "true", "yes")

# ── Single-thread executor — ALL MT5 calls MUST go through this ──────────────
_MT5_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="mt5-com",
)

try:
    if ISOLATED_MT5:
        mt5 = None  # type: ignore[assignment]
    else:
        import MetaTrader5 as mt5  # type: ignore[import-untyped]
except ImportError:
    mt5 = None  # type: ignore[assignment]

_TF_MAP: dict[str, int] = {}
if mt5 is not None:
    _TF_MAP = {
        "M1":  mt5.TIMEFRAME_M1,
        "M5":  mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1":  mt5.TIMEFRAME_H1,
        "H4":  mt5.TIMEFRAME_H4,
        "D1":  mt5.TIMEFRAME_D1,
    }

RECONNECT_DELAY_SEC = 5
MAX_RECONNECT_ATTEMPTS = 10


def parse_mt5_symbol_aliases() -> dict[str, str]:
    raw = os.getenv("MT5_SYMBOL_ALIASES", "").strip()
    out: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            key = k.strip().upper().replace("/", "")
            val = v.strip()
            if key and val:
                out[key] = val
    return out


async def _mt5(fn: Callable, *args, **kwargs):
    """Run *fn* on the dedicated MT5 COM thread and return the result."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_MT5_EXECUTOR, lambda: fn(*args, **kwargs))


_SECRET_KEYS = frozenset({"password", "pass", "token", "api_key", "secret"})


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy of *payload* with credential values masked."""
    return {
        k: ("***REDACTED***" if k.lower() in _SECRET_KEYS else v)
        for k, v in payload.items()
    }


async def _mt5_worker(payload: dict[str, Any], timeout_sec: float = 20.0) -> dict[str, Any]:
    """Run an MT5 call in a short-lived subprocess.

    This isolates native MetaTrader5 crashes from the nexus-data process.
    """
    worker = Path(__file__).with_name("mt5_worker.py")

    # SECURITY: credentials reach the worker only through the inherited process
    # environment, never the stdin payload. The payload is the argument to
    # asyncio's communicate(), whose internal `input` local is dumped verbatim
    # by rich tracebacks on any subprocess error/timeout — that is what printed
    # the broker password in clear text in the logs. Env vars live in
    # os.environ (a module global), which tracebacks never dump as a frame
    # local. We promote any creds that still arrive in the payload into
    # os.environ so the short-lived worker inherits them (subprocess with no
    # env= copies the parent environment), then redact the payload before it is
    # ever serialized — so neither a frame local nor the stdin bytes carry the
    # secret. Do NOT bind the credentials to a local here (e.g. a child_env
    # dict): such a local is exactly what the rich traceback would print.
    for src_key, env_key in (
        ("login", "MT5_LOGIN"),
        ("password", "MT5_PASSWORD"),
        ("server", "MT5_SERVER"),
    ):
        if payload.get(src_key) and not os.environ.get(env_key):
            os.environ[env_key] = str(payload[src_key])
    payload = _redact(payload)

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(worker),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(json.dumps(payload, default=str).encode("utf-8")),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError("MT5 worker timed out")

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        out = stdout.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"MT5 worker failed ({proc.returncode}): {out or err}")

    try:
        data = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"MT5 worker returned invalid JSON: {exc}") from exc
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error") or "MT5 worker failed"))
    return data


class MT5Connector:
    """Async wrapper around the MetaTrader5 terminal library."""

    def __init__(self) -> None:
        self._connected: bool = False
        self._login: int = 0
        self._password: str = ""
        self._server: str = ""
        self._path: str = ""
        self._reconnect_attempts: int = 0

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self, login: int, password: str, server: str, path: str = "") -> None:
        if mt5 is None and not ISOLATED_MT5:
            raise RuntimeError("MetaTrader5 package is not installed (Windows-only)")
        self._login = login
        self._password = password
        self._server = server
        self._path = path
        await self._init_terminal()

    async def _init_terminal(self) -> None:
        if ISOLATED_MT5:
            await _mt5_worker({
                "op": "ping",
                "login": self._login,
                "password": self._password,
                "server": self._server,
                "path": self._path,
            })
            self._connected = True
            self._reconnect_attempts = 0
            await log.ainfo(
                "mt5.connected_isolated",
                login=self._login,
                server=self._server,
            )
            return

        kwargs: dict[str, Any] = {
            "login": self._login,
            "password": self._password,
            "server": self._server,
        }
        if self._path:
            kwargs["path"] = self._path

        def _do_init():
            assert mt5 is not None
            return mt5.initialize(**kwargs)

        ok = await _mt5(_do_init)
        if not ok:
            def _last_err():
                assert mt5 is not None
                return mt5.last_error()
            code, msg = await _mt5(_last_err)
            self._connected = False
            raise ConnectionError(f"MT5 initialize failed: [{code}] {msg}")

        self._connected = True
        self._reconnect_attempts = 0
        await log.ainfo("mt5.connected", login=self._login, server=self._server)

    async def _reconnect(self) -> bool:
        while self._reconnect_attempts < MAX_RECONNECT_ATTEMPTS:
            self._reconnect_attempts += 1
            await log.awarning(
                "mt5.reconnecting",
                attempt=self._reconnect_attempts,
                max_attempts=MAX_RECONNECT_ATTEMPTS,
            )
            try:
                await self._init_terminal()
                return True
            except ConnectionError:
                await asyncio.sleep(RECONNECT_DELAY_SEC)
        await log.aerror("mt5.reconnect_exhausted")
        return False

    async def disconnect(self) -> None:
        if ISOLATED_MT5:
            self._connected = False
            await log.ainfo("mt5.disconnected_isolated")
            return
        if mt5 is not None and self._connected:
            await _mt5(mt5.shutdown)
            self._connected = False
            await log.ainfo("mt5.disconnected")

    def resolve_symbol(self, logical: str) -> str:
        s = logical.upper().replace("/", "")
        return parse_mt5_symbol_aliases().get(s, s)

    async def _ensure_symbol_selected(self, broker_symbol: str) -> None:
        self._ensure_connected()
        if ISOLATED_MT5:
            return

        def _sel():
            assert mt5 is not None
            if not mt5.symbol_select(broker_symbol, True):
                raise ValueError(
                    f"MT5 symbol_select failed for {broker_symbol!r} — add it in Market Watch "
                    f"or set MT5_SYMBOL_ALIASES (e.g. XAUUSD=XAUUSDm)."
                )

        await _mt5(_sel)

    async def get_tick(self, symbol: str) -> Tick:
        self._ensure_connected()
        logical = symbol.upper().replace("/", "")
        resolved = self.resolve_symbol(logical)
        if ISOLATED_MT5:
            data = await _mt5_worker({
                "op": "tick",
                "login": self._login,
                "password": self._password,
                "server": self._server,
                "path": self._path,
                "symbol": logical,
                "broker_symbol": resolved,
            }, timeout_sec=10.0)
            tick = data["tick"]
            return Tick(
                symbol=logical,
                bid=float(tick["bid"]),
                ask=float(tick["ask"]),
                timestamp=datetime.fromisoformat(tick["timestamp"]),
            )
        await self._ensure_symbol_selected(resolved)

        def _fetch():
            assert mt5 is not None
            return mt5.symbol_info_tick(resolved)

        info = await _mt5(_fetch)
        if info is None:
            raise ValueError(f"MT5 returned no tick for {resolved} (logical={logical})")
        return Tick(
            symbol=logical,
            bid=info.bid,
            ask=info.ask,
            timestamp=datetime.fromtimestamp(info.time, tz=timezone.utc),
        )

    async def get_ohlcv(self, symbol: str, timeframe: str, count: int = 500) -> list[OHLCV]:
        self._ensure_connected()
        logical = symbol.upper().replace("/", "")
        resolved = self.resolve_symbol(logical)
        tf = _TF_MAP.get(timeframe)
        if not ISOLATED_MT5 and tf is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        if ISOLATED_MT5:
            data = await _mt5_worker({
                "op": "ohlcv",
                "login": self._login,
                "password": self._password,
                "server": self._server,
                "path": self._path,
                "symbol": logical,
                "broker_symbol": resolved,
                "timeframe": timeframe,
                "count": count,
            }, timeout_sec=20.0)
            return [
                OHLCV(
                    symbol=logical,
                    timeframe=timeframe,
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=float(r["volume"]),
                    timestamp=datetime.fromisoformat(r["timestamp"]),
                )
                for r in data["bars"]
            ]

        await self._ensure_symbol_selected(resolved)

        def _fetch():
            assert mt5 is not None
            return mt5.copy_rates_from_pos(resolved, tf, 0, count)

        rates = await _mt5(_fetch)
        if rates is None or len(rates) == 0:
            raise ValueError(
                f"MT5 returned no OHLCV for {resolved}/{timeframe} (logical={logical})"
            )
        return [
            OHLCV(
                symbol=logical,
                timeframe=timeframe,
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=float(r["tick_volume"]),
                timestamp=datetime.fromtimestamp(r["time"], tz=timezone.utc),
            )
            for r in rates
        ]

    async def get_account_info(self) -> dict[str, Any]:
        self._ensure_connected()
        if ISOLATED_MT5:
            data = await _mt5_worker({
                "op": "account",
                "login": self._login,
                "password": self._password,
                "server": self._server,
                "path": self._path,
            }, timeout_sec=10.0)
            return data["account"]

        def _fetch():
            assert mt5 is not None
            info = mt5.account_info()
            if info is None:
                raise RuntimeError("MT5 account_info returned None")
            return info._asdict()

        return await _mt5(_fetch)

    async def stream_ticks(
        self,
        symbols: list[str],
        callback: Callable[[Tick], Awaitable[None]],
        poll_interval: float = 0.5,
    ) -> None:
        last_times: dict[str, int] = {}
        while self._connected:
            for symbol in symbols:
                try:
                    logical = symbol.upper().replace("/", "")
                    tick = await self.get_tick(logical)
                    ts_key = int(tick.timestamp.timestamp() * 1000)
                    if ts_key != last_times.get(symbol):
                        last_times[symbol] = ts_key
                        await callback(tick)
                except Exception:
                    await log.aexception("mt5.stream_tick_error", symbol=symbol)
                    if not self._connected:
                        recovered = await self._reconnect()
                        if not recovered:
                            return
            await asyncio.sleep(poll_interval)

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("MT5Connector is not connected")
