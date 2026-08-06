"""
Yahoo Finance historical data connector (free backup source).

Uses the ``yfinance`` library to pull historical OHLCV data.  This is
a secondary / fallback source — not suitable for real-time streaming.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import structlog

log = structlog.get_logger("nexus_data.yfinance")

try:
    import pandas as pd
    import yfinance as yf  # type: ignore[import-untyped]
except ImportError:
    yf = None  # type: ignore[assignment]
    pd = None  # type: ignore[assignment]

_THREAD_POOL = ThreadPoolExecutor(max_workers=4)


class YFinanceConnector:
    """Thin async wrapper around ``yfinance.download``."""

    # ── Single symbol ────────────────────────────────────────────────────

    async def get_historical(
        self,
        symbol: str,
        period: str = "1mo",
        interval: str = "1d",
    ) -> Any:
        """Return a ``pandas.DataFrame`` with OHLCV columns.

        Parameters
        ----------
        symbol:
            Ticker symbol (e.g. ``"AAPL"``, ``"EURUSD=X"``, ``"BTC-USD"``).
        period:
            yfinance period string — ``"1d"``, ``"5d"``, ``"1mo"``,
            ``"3mo"``, ``"6mo"``, ``"1y"``, ``"2y"``, ``"5y"``, ``"10y"``,
            ``"ytd"``, ``"max"``.
        interval:
            Bar size — ``"1m"``, ``"2m"``, ``"5m"``, ``"15m"``, ``"30m"``,
            ``"60m"``, ``"90m"``, ``"1h"``, ``"1d"``, ``"5d"``, ``"1wk"``,
            ``"1mo"``, ``"3mo"``.
        """
        if yf is None:
            raise RuntimeError("yfinance package is not installed")

        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(
            _THREAD_POOL,
            lambda: yf.download(
                symbol,
                period=period,
                interval=interval,
                progress=False,
                auto_adjust=True,
            ),
        )

        if df is None or df.empty:
            await log.awarning(
                "yfinance.no_data", symbol=symbol, period=period,
            )
            return pd.DataFrame()

        await log.ainfo(
            "yfinance.downloaded",
            symbol=symbol,
            rows=len(df),
            period=period,
            interval=interval,
        )
        return df

    # ── Batch download ───────────────────────────────────────────────────

    async def batch_download(
        self,
        symbols: list[str],
        period: str = "1mo",
        interval: str = "1d",
    ) -> dict[str, Any]:
        """Download historical data for multiple symbols concurrently.

        Returns a ``dict`` mapping each symbol to its ``DataFrame``.
        """
        if yf is None:
            raise RuntimeError("yfinance package is not installed")

        tasks = [
            self.get_historical(sym, period=period, interval=interval)
            for sym in symbols
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: dict[str, Any] = {}
        for sym, res in zip(symbols, results):
            if isinstance(res, Exception):
                await log.awarning(
                    "yfinance.batch_error", symbol=sym, error=str(res),
                )
                output[sym] = pd.DataFrame() if pd else None
            else:
                output[sym] = res

        await log.ainfo(
            "yfinance.batch_complete",
            requested=len(symbols),
            succeeded=sum(
                1 for v in output.values()
                if v is not None and not getattr(v, "empty", True)
            ),
        )
        return output
