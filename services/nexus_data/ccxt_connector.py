"""
Crypto exchange data connector via CCXT (sync + async).

Supports Binance, Bybit, OKX and any other exchange supported by CCXT.
Uses WebSocket streaming when available.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import structlog

from shared.models import Tick, OHLCV

log = structlog.get_logger("nexus_data.ccxt")

try:
    import ccxt.async_support as ccxt_async  # type: ignore[import-untyped]
    import ccxt.pro as ccxt_pro  # type: ignore[import-untyped]
except ImportError:
    ccxt_async = None  # type: ignore[assignment]
    ccxt_pro = None  # type: ignore[assignment]

SUPPORTED_EXCHANGES = {"binance", "bybit", "okx"}

RECONNECT_DELAY_SEC = 5
MAX_RECONNECT_ATTEMPTS = 10

_TF_MAP: dict[str, str] = {
    "M1": "1m",
    "M5": "5m",
    "M15": "15m",
    "M30": "30m",
    "H1": "1h",
    "H4": "4h",
    "D1": "1d",
}


class CCXTConnector:
    """Async crypto exchange connector with REST + WebSocket support."""

    def __init__(self) -> None:
        self._exchange: Any | None = None
        self._ws_exchange: Any | None = None
        self._exchange_name: str = ""
        self._connected: bool = False
        self._reconnect_attempts: int = 0
        self._api_key: str = ""
        self._secret: str = ""

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def connect(
        self,
        exchange_name: str = "binance",
        api_key: str = "",
        secret: str = "",
    ) -> None:
        if ccxt_async is None:
            raise RuntimeError("ccxt package is not installed")
        if exchange_name not in SUPPORTED_EXCHANGES:
            raise ValueError(
                f"Exchange '{exchange_name}' not in {SUPPORTED_EXCHANGES}"
            )

        self._exchange_name = exchange_name
        self._api_key = api_key
        self._secret = secret

        await self._create_exchange()

    async def _create_exchange(self) -> None:
        exchange_cls = getattr(ccxt_async, self._exchange_name, None)
        if exchange_cls is None:
            raise ValueError(f"CCXT has no exchange '{self._exchange_name}'")

        config: dict[str, Any] = {"enableRateLimit": True}
        if self._api_key:
            config["apiKey"] = self._api_key
        if self._secret:
            config["secret"] = self._secret

        self._exchange = exchange_cls(config)
        await self._exchange.load_markets()

        # WebSocket exchange (ccxt.pro) — best-effort
        if ccxt_pro is not None:
            try:
                ws_cls = getattr(ccxt_pro, self._exchange_name, None)
                if ws_cls is not None:
                    self._ws_exchange = ws_cls(config)
            except Exception:
                await log.awarning("ccxt.ws_init_skipped")

        self._connected = True
        self._reconnect_attempts = 0
        await log.ainfo(
            "ccxt.connected",
            exchange=self._exchange_name,
            markets=len(self._exchange.markets),
        )

    async def _reconnect(self) -> bool:
        while self._reconnect_attempts < MAX_RECONNECT_ATTEMPTS:
            self._reconnect_attempts += 1
            await log.awarning(
                "ccxt.reconnecting",
                attempt=self._reconnect_attempts,
            )
            try:
                await self._create_exchange()
                return True
            except Exception:
                await asyncio.sleep(RECONNECT_DELAY_SEC)
        await log.aerror("ccxt.reconnect_exhausted")
        return False

    async def disconnect(self) -> None:
        if self._ws_exchange is not None:
            try:
                await self._ws_exchange.close()
            except Exception:
                pass
            self._ws_exchange = None
        if self._exchange is not None:
            try:
                await self._exchange.close()
            except Exception:
                pass
            self._exchange = None
        self._connected = False
        await log.ainfo("ccxt.disconnected")

    # ── Data retrieval ───────────────────────────────────────────────────

    async def get_tick(self, symbol: str) -> Tick:
        self._ensure_connected()
        ticker = await self._exchange.fetch_ticker(symbol)
        return Tick(
            symbol=symbol,
            bid=float(ticker.get("bid", 0) or 0),
            ask=float(ticker.get("ask", 0) or 0),
            timestamp=datetime.fromtimestamp(
                ticker["timestamp"] / 1000, tz=timezone.utc,
            )
            if ticker.get("timestamp")
            else datetime.now(timezone.utc),
        )

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        count: int = 500,
    ) -> list[OHLCV]:
        self._ensure_connected()
        ccxt_tf = _TF_MAP.get(timeframe, timeframe)
        raw = await self._exchange.fetch_ohlcv(
            symbol, timeframe=ccxt_tf, limit=count,
        )
        return [
            OHLCV(
                symbol=symbol,
                timeframe=timeframe,
                open=float(candle[1]),
                high=float(candle[2]),
                low=float(candle[3]),
                close=float(candle[4]),
                volume=float(candle[5]),
                timestamp=datetime.fromtimestamp(
                    candle[0] / 1000, tz=timezone.utc,
                ),
            )
            for candle in raw
        ]

    # ── Streaming ────────────────────────────────────────────────────────

    async def stream_ticks(
        self,
        symbols: list[str],
        callback: Callable[[Tick], Awaitable[None]],
    ) -> None:
        """Stream ticks via WebSocket (ccxt.pro) with REST fallback."""
        if self._ws_exchange is not None:
            await self._stream_ws(symbols, callback)
        else:
            await self._stream_poll(symbols, callback)

    async def _stream_ws(
        self,
        symbols: list[str],
        callback: Callable[[Tick], Awaitable[None]],
    ) -> None:
        while self._connected and self._ws_exchange is not None:
            for symbol in symbols:
                try:
                    ticker = await self._ws_exchange.watch_ticker(symbol)
                    tick = Tick(
                        symbol=symbol,
                        bid=float(ticker.get("bid", 0) or 0),
                        ask=float(ticker.get("ask", 0) or 0),
                        timestamp=datetime.fromtimestamp(
                            ticker["timestamp"] / 1000, tz=timezone.utc,
                        )
                        if ticker.get("timestamp")
                        else datetime.now(timezone.utc),
                    )
                    await callback(tick)
                except asyncio.CancelledError:
                    return
                except Exception:
                    await log.aexception("ccxt.ws_error", symbol=symbol)
                    if not self._connected:
                        return
                    recovered = await self._reconnect()
                    if not recovered:
                        return

    async def _stream_poll(
        self,
        symbols: list[str],
        callback: Callable[[Tick], Awaitable[None]],
        poll_interval: float = 1.0,
    ) -> None:
        """Fallback REST polling when WebSocket is unavailable."""
        while self._connected:
            for symbol in symbols:
                try:
                    tick = await self.get_tick(symbol)
                    await callback(tick)
                except asyncio.CancelledError:
                    return
                except Exception:
                    await log.aexception("ccxt.poll_error", symbol=symbol)
            await asyncio.sleep(poll_interval)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _ensure_connected(self) -> None:
        if not self._connected or self._exchange is None:
            raise RuntimeError("CCXTConnector is not connected")
