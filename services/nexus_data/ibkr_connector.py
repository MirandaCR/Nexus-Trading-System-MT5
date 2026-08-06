"""
Interactive Brokers data connector via ``ib_insync``.

Provides the same Tick / OHLCV interface as the MT5 connector so the
data service can treat brokers interchangeably.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import structlog

from shared.models import Tick, OHLCV

log = structlog.get_logger("nexus_data.ibkr")

try:
    from ib_insync import IB, Contract, Forex, Stock, util  # type: ignore[import-untyped]
except ImportError:
    IB = None  # type: ignore[assignment,misc]

RECONNECT_DELAY_SEC = 5
MAX_RECONNECT_ATTEMPTS = 10

# Common forex pairs mapped to ib_insync Forex contracts
_FOREX_SYMBOLS = {
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
    "AUDUSD", "NZDUSD", "USDCAD", "EURGBP",
    "EURJPY", "GBPJPY", "AUDJPY",
}

_DURATION_MAP: dict[str, str] = {
    "M1": "2 D",
    "M5": "5 D",
    "M15": "10 D",
    "M30": "20 D",
    "H1": "30 D",
    "H4": "60 D",
    "D1": "365 D",
}

_BAR_SIZE_MAP: dict[str, str] = {
    "M1": "1 min",
    "M5": "5 mins",
    "M15": "15 mins",
    "M30": "30 mins",
    "H1": "1 hour",
    "H4": "4 hours",
    "D1": "1 day",
}


def _make_contract(symbol: str) -> Any:
    """Build an ``ib_insync`` contract object from a symbol string."""
    if symbol.upper() in _FOREX_SYMBOLS:
        pair = symbol.upper()
        return Forex(pair[:3], pair[3:])
    return Stock(symbol, "SMART", "USD")


class IBKRConnector:
    """Async wrapper around the Interactive Brokers TWS / Gateway API."""

    def __init__(self) -> None:
        self._ib: Any | None = None
        self._connected: bool = False
        self._host: str = "127.0.0.1"
        self._port: int = 7497
        self._client_id: int = 1
        self._reconnect_attempts: int = 0
        self._tick_subscriptions: dict[str, Any] = {}

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def connect(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
    ) -> None:
        if IB is None:
            raise RuntimeError("ib_insync package is not installed")

        self._host = host
        self._port = port
        self._client_id = client_id

        await self._establish_connection()

    async def _establish_connection(self) -> None:
        self._ib = IB()
        await self._ib.connectAsync(
            self._host, self._port, clientId=self._client_id,
        )
        self._connected = True
        self._reconnect_attempts = 0
        await log.ainfo(
            "ibkr.connected",
            host=self._host,
            port=self._port,
            client_id=self._client_id,
        )

    async def _reconnect(self) -> bool:
        while self._reconnect_attempts < MAX_RECONNECT_ATTEMPTS:
            self._reconnect_attempts += 1
            await log.awarning(
                "ibkr.reconnecting",
                attempt=self._reconnect_attempts,
            )
            try:
                await self._establish_connection()
                return True
            except Exception:
                await asyncio.sleep(RECONNECT_DELAY_SEC)
        await log.aerror("ibkr.reconnect_exhausted")
        return False

    async def disconnect(self) -> None:
        if self._ib is not None and self._connected:
            for ticker in self._tick_subscriptions.values():
                self._ib.cancelMktData(ticker.contract)
            self._tick_subscriptions.clear()
            self._ib.disconnect()
            self._connected = False
            await log.ainfo("ibkr.disconnected")

    # ── Data retrieval ───────────────────────────────────────────────────

    async def get_tick(self, symbol: str) -> Tick:
        self._ensure_connected()
        contract = _make_contract(symbol)
        self._ib.qualifyContracts(contract)
        ticker = self._ib.reqMktData(contract, genericTickList="", snapshot=True)

        for _ in range(50):
            await asyncio.sleep(0.1)
            if ticker.bid and ticker.ask:
                break

        bid = float(ticker.bid) if ticker.bid else 0.0
        ask = float(ticker.ask) if ticker.ask else 0.0

        self._ib.cancelMktData(contract)

        return Tick(
            symbol=symbol,
            bid=bid,
            ask=ask,
            timestamp=datetime.now(timezone.utc),
        )

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        count: int = 500,
    ) -> list[OHLCV]:
        self._ensure_connected()
        contract = _make_contract(symbol)
        self._ib.qualifyContracts(contract)

        duration = _DURATION_MAP.get(timeframe, "30 D")
        bar_size = _BAR_SIZE_MAP.get(timeframe, "1 hour")

        bars = await self._ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="MIDPOINT",
            useRTH=False,
            formatDate=2,
        )

        results = [
            OHLCV(
                symbol=symbol,
                timeframe=timeframe,
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume) if bar.volume >= 0 else 0.0,
                timestamp=bar.date if isinstance(bar.date, datetime)
                else datetime.fromisoformat(str(bar.date)).replace(
                    tzinfo=timezone.utc,
                ),
            )
            for bar in bars
        ]
        return results[-count:]

    # ── Streaming ────────────────────────────────────────────────────────

    async def stream_ticks(
        self,
        symbols: list[str],
        callback: Callable[[Tick], Awaitable[None]],
    ) -> None:
        """Subscribe to real-time market data for *symbols*."""
        self._ensure_connected()

        for symbol in symbols:
            contract = _make_contract(symbol)
            self._ib.qualifyContracts(contract)
            ticker = self._ib.reqMktData(contract)
            self._tick_subscriptions[symbol] = ticker

        while self._connected:
            for symbol, ticker in list(self._tick_subscriptions.items()):
                try:
                    bid = float(ticker.bid) if ticker.bid else 0.0
                    ask = float(ticker.ask) if ticker.ask else 0.0
                    if bid > 0 and ask > 0:
                        tick = Tick(
                            symbol=symbol,
                            bid=bid,
                            ask=ask,
                            timestamp=datetime.now(timezone.utc),
                        )
                        await callback(tick)
                except Exception:
                    await log.aexception("ibkr.stream_error", symbol=symbol)
                    if not self._connected:
                        recovered = await self._reconnect()
                        if not recovered:
                            return

            self._ib.sleep(0.1)
            await asyncio.sleep(0)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _ensure_connected(self) -> None:
        if not self._connected or self._ib is None:
            raise RuntimeError("IBKRConnector is not connected")
