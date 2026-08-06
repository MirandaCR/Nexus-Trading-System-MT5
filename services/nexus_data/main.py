"""
Nexus Data Service — FastAPI application.

Exposes tick / OHLCV query endpoints and manages the lifecycle of every
data-feed connector, the DuckDB store, and the Redis tick cache.
"""
from __future__ import annotations

import asyncio
import math
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from shared.constants import RedisStream, SERVICE_PORTS, SUPPORTED_TIMEFRAMES
from shared.events import TickEvent, OHLCVEvent
from shared.models import Tick, OHLCV
from shared.nexus_trades import strategy_from_comment as _strategy_from_comment
from shared.redis_client import RedisClient

from .duckdb_store import DataStore
from .redis_cache import TickCache
from .mt5_connector import MT5Connector
from .ibkr_connector import IBKRConnector
from .ccxt_connector import CCXTConnector

from shared import setup_logger as _setup_logger
_setup_logger("nexus-data")
log = structlog.get_logger("nexus_data")

# ── Configuration from env ───────────────────────────────────────────────────

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DUCKDB_PATH = os.getenv("DUCKDB_PATH", "./data/nexus.duckdb")

MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
MT5_PATH = os.getenv("MT5_PATH", "")

IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "7497"))
IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "1"))

CCXT_EXCHANGE = os.getenv("CCXT_EXCHANGE", "binance")
CCXT_API_KEY = os.getenv("CCXT_API_KEY", "")
CCXT_SECRET = os.getenv("CCXT_SECRET", "")

DEFAULT_BROKER = os.getenv("DEFAULT_BROKER", "mt5")
# Production default: MT5-only real data. Set NEXUS_DATA_SYNTHETIC_FALLBACK=1 only for offline dev.
SYNTHETIC_FALLBACK = os.getenv("NEXUS_DATA_SYNTHETIC_FALLBACK", "0").lower() in (
    "1",
    "true",
    "yes",
)

# Symbols to stream on startup (comma-separated env var); logical names (aliases → broker via MT5_SYMBOL_ALIASES).
STREAM_SYMBOLS = [
    s.strip().upper().replace("/", "")
    for s in os.getenv(
        "STREAM_SYMBOLS", "XAUUSD"
    ).split(",")
    if s.strip()
]

# ── Shared instances ─────────────────────────────────────────────────────────

redis_client = RedisClient(url=REDIS_URL)
data_store = DataStore(db_path=DUCKDB_PATH)
tick_cache = TickCache(redis_client)

mt5 = MT5Connector()
ibkr = IBKRConnector()
ccxt_conn = CCXTConnector()

_streaming_tasks: list[asyncio.Task] = []


def _base_price(symbol: str) -> float:
    s = symbol.upper()
    if s.startswith("XAU"):
        return 2350.0
    if s.startswith("BTC"):
        return 65000.0
    if "JPY" in s:
        return 155.0
    return 1.08


def _synthetic_tick(symbol: str) -> Tick:
    now = datetime.now(timezone.utc)
    seed = sum(ord(c) for c in symbol)
    base = _base_price(symbol)
    wiggle = math.sin(now.timestamp() / 60 + seed) * base * 0.0005
    bid = base + wiggle
    spread = 0.25 if symbol.startswith("XAU") else 0.00015
    return Tick(symbol=symbol, bid=bid, ask=bid + spread, timestamp=now)


def _synthetic_ohlcv(symbol: str, timeframe: str, limit: int) -> list[OHLCV]:
    minutes = {
        "M1": 1,
        "M5": 5,
        "M15": 15,
        "M30": 30,
        "H1": 60,
        "H4": 240,
        "D1": 1440,
    }.get(timeframe, 15)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    base = _base_price(symbol)
    amp = base * (0.0015 if not symbol.startswith("BTC") else 0.004)
    seed = sum(ord(c) for c in symbol + timeframe)
    out: list[OHLCV] = []
    for i in range(limit - 1, -1, -1):
        ts = now - timedelta(minutes=minutes * i)
        x = (limit - i + seed) / 8.0
        close = base + math.sin(x) * amp + math.sin(x / 3) * amp * 0.5
        open_ = base + math.sin(x - 0.2) * amp + math.sin((x - 0.2) / 3) * amp * 0.5
        high = max(open_, close) + amp * 0.2
        low = min(open_, close) - amp * 0.2
        out.append(OHLCV(
            symbol=symbol,
            timeframe=timeframe,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=100 + abs(math.sin(x)) * 900,
            timestamp=ts,
        ))
    return out


# ── Tick ingestion callback ──────────────────────────────────────────────────

async def _on_tick(tick: Tick) -> None:
    """Fan-out every incoming tick to cache, DuckDB, and Redis Stream."""
    try:
        await asyncio.gather(
            tick_cache.cache_tick(tick),
            data_store.insert_tick(tick),
            redis_client.publish_event(
                RedisStream.TICK_STREAM,
                TickEvent(
                    source="nexus-data",
                    symbol=tick.symbol,
                    bid=tick.bid,
                    ask=tick.ask,
                    spread=tick.spread,
                ),
            ),
        )
    except Exception:
        await log.aexception("tick_ingest_error", symbol=tick.symbol)


async def _trade_close_consumer() -> None:
    """Consume TradeCloseEvent from Redis Stream and persist to DuckDB.

    Bridges the gap between the executor (publishes close events) and the
    API gateway's trades table so the dashboard history page works.
    """
    import json as _json
    import uuid

    CONSUMER_GROUP = "nexus-data-trades-cg"
    CONSUMER_NAME = f"data-trade-worker-{uuid.uuid4().hex[:6]}"
    STREAM = RedisStream.TRADE_CLOSE

    await asyncio.sleep(5)

    try:
        await redis_client.r.xgroup_create(STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            await log.aexception("trade_consumer.group_create_failed")

    await log.ainfo("trade_consumer.started", stream=STREAM)

    while True:
        try:
            entries = await redis_client.r.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername=CONSUMER_NAME,
                streams={STREAM: ">"},
                count=10,
                block=3000,
            )
            if not entries:
                continue

            for _stream_name, messages in entries:
                for msg_id, raw in messages:
                    try:
                        trade_id = raw.get("trade_id", raw.get("order_id", msg_id))
                        symbol = raw.get("symbol", "")
                        side = raw.get("side", "BUY")
                        entry_price = float(raw.get("entry_price", 0))
                        exit_price = float(raw.get("exit_price", 0))
                        pnl_pips = float(raw.get("pnl_pips", 0))
                        pnl_usd = float(raw.get("pnl_usd", 0))
                        rr_ratio = float(raw.get("rr_ratio", 0))
                        reason = raw.get("reason", "")

                        if not symbol:
                            await redis_client.r.xack(STREAM, CONSUMER_GROUP, msg_id)
                            continue

                        if exit_price == 0 and entry_price > 0 and pnl_pips != 0:
                            from shared.asset_specs import pips_to_price
                            signed = pnl_pips if side.upper() == "BUY" else -pnl_pips
                            exit_price = entry_price + pips_to_price(symbol, signed)
                        if exit_price == 0 and pnl_usd == 0 and pnl_pips == 0:
                            await redis_client.r.xack(STREAM, CONSUMER_GROUP, msg_id)
                            continue

                        now = datetime.now(timezone.utc)
                        opened_str = (
                            raw.get("opened_at")
                            or raw.get("data.opened_at")
                            or raw.get("timestamp")
                            or now.isoformat()
                        )
                        if not opened_str or opened_str == "0" or opened_str == "":
                            opened_str = now.isoformat()
                        try:
                            opened_at = datetime.fromisoformat(str(opened_str).replace("Z", "+00:00"))
                            if opened_at.tzinfo is None:
                                opened_at = opened_at.replace(tzinfo=timezone.utc)
                        except Exception:
                            opened_at = now
                        closed_at = now
                        # Broker clocks can drift/skew (server-time epochs); a
                        # future opened_at would store negative durations.
                        if opened_at > closed_at:
                            await log.awarning(
                                "trade_consumer.opened_after_closed",
                                trade_id=trade_id,
                                opened_at=str(opened_at),
                            )
                            opened_at = closed_at
                        duration_s = max(0.0, (closed_at - opened_at).total_seconds())

                        strategy = str(
                            raw.get("data.strategy", "")
                            or _strategy_from_comment(str(raw.get("data.source", "")))
                        )

                        await data_store.insert_trade(
                            trade_id=str(trade_id),
                            symbol=symbol,
                            side=side,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            pnl_pips=pnl_pips,
                            pnl_usd=pnl_usd,
                            rr_ratio=rr_ratio,
                            duration_s=duration_s,
                            session=raw.get("session", ""),
                            conviction=float(raw.get("conviction", 0)),
                            opened_at=opened_at,
                            closed_at=closed_at,
                            strategy=strategy,
                        )
                        await log.ainfo(
                            "trade_consumer.persisted",
                            trade_id=trade_id,
                            symbol=symbol,
                            side=side,
                            pnl_usd=pnl_usd,
                        )
                    except Exception:
                        await log.aexception("trade_consumer.process_error", msg_id=msg_id)
                    finally:
                        await redis_client.r.xack(STREAM, CONSUMER_GROUP, msg_id)

        except asyncio.CancelledError:
            break
        except Exception:
            await log.aexception("trade_consumer.loop_error")
            await asyncio.sleep(5)


OHLCV_POLL_SEC = int(os.getenv("NEXUS_OHLCV_POLL_SEC", "30"))
# Comma-separated list of timeframes to stream. Scalping needs M5 (primary
# decision TF) plus M15/H1 (confirmation TFs). A single value also works.
OHLCV_TIMEFRAMES = [
    tf.strip().upper()
    for tf in os.getenv("NEXUS_OHLCV_TIMEFRAME", "M15").split(",")
    if tf.strip()
] or ["M15"]
OHLCV_BAR_COUNT = int(os.getenv("NEXUS_OHLCV_BAR_COUNT", "300"))

import json as _json

async def _ohlcv_publisher() -> None:
    """Fetch full OHLCV history from MT5, cache in Redis, publish stream event.

    nexus-features reads the cached bars to compute indicators.
    """
    await asyncio.sleep(3)
    consecutive_failures = 0

    while True:
        for symbol in STREAM_SYMBOLS:
            for tf in OHLCV_TIMEFRAMES:
                try:
                    bars = await mt5.get_ohlcv(symbol, tf, OHLCV_BAR_COUNT)
                    if not bars:
                        await log.awarning("ohlcv.no_bars", symbol=symbol, tf=tf)
                        continue

                    bars_json = [
                        {
                            "timestamp": b.timestamp.isoformat(),
                            "open": b.open,
                            "high": b.high,
                            "low": b.low,
                            "close": b.close,
                            "volume": b.volume,
                        }
                        for b in bars
                    ]

                    cache_key = f"nexus:ohlcv:{symbol}:{tf}"
                    await redis_client.r.set(cache_key, _json.dumps(bars_json), ex=3600)

                    last_bar = bars[-1]
                    await redis_client.publish_event(
                        RedisStream.OHLCV_STREAM,
                        OHLCVEvent(
                            source="nexus-data",
                            symbol=last_bar.symbol,
                            timeframe=last_bar.timeframe,
                            open=last_bar.open,
                            high=last_bar.high,
                            low=last_bar.low,
                            close=last_bar.close,
                            volume=last_bar.volume,
                        ),
                    )
                    consecutive_failures = 0
                    await log.ainfo(
                        "ohlcv.published",
                        symbol=symbol,
                        tf=tf,
                        bars=len(bars),
                        close=round(last_bar.close, 2),
                    )
                except Exception:
                    consecutive_failures += 1
                    await log.aexception(
                        "ohlcv_publisher_error",
                        symbol=symbol,
                        tf=tf,
                        failures=consecutive_failures,
                    )
                    if consecutive_failures >= 3 and mt5.connected:
                        await log.awarning("ohlcv.reconnecting_mt5")
                        try:
                            await mt5.disconnect()
                            await asyncio.sleep(2)
                            await mt5.connect(
                                login=MT5_LOGIN,
                                password=MT5_PASSWORD,
                                server=MT5_SERVER,
                                path=MT5_PATH,
                            )
                            consecutive_failures = 0
                        except Exception:
                            await log.aexception("ohlcv.reconnect_failed")

        await asyncio.sleep(OHLCV_POLL_SEC)


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await log.ainfo("startup.begin")

    # 1. Redis
    await redis_client.connect()
    await log.ainfo("startup.redis_connected")

    # 2. DuckDB (must finish before yield — run.py gates other services on this)
    try:
        await data_store.init_tables()
    except Exception:
        await log.aexception("startup.duckdb_failed")
        raise
    await log.ainfo("startup.duckdb_ready")

    # 3. Broker feeds in background so /health responds while MT5 connects
    async def _start_broker_feeds() -> None:
        if DEFAULT_BROKER == "mt5" and MT5_LOGIN:
            try:
                await mt5.connect(
                    login=MT5_LOGIN,
                    password=MT5_PASSWORD,
                    server=MT5_SERVER,
                    path=MT5_PATH,
                )
                task = asyncio.create_task(
                    mt5.stream_ticks(STREAM_SYMBOLS, _on_tick)
                )
                _streaming_tasks.append(task)

                ohlcv_task = asyncio.create_task(_ohlcv_publisher())
                _streaming_tasks.append(ohlcv_task)

                await log.ainfo("startup.mt5_streaming", symbols=STREAM_SYMBOLS)
            except Exception:
                await log.aexception("startup.mt5_failed")

        if IBKR_HOST and os.getenv("ENABLE_IBKR", ""):
            try:
                await ibkr.connect(
                    host=IBKR_HOST,
                    port=IBKR_PORT,
                    client_id=IBKR_CLIENT_ID,
                )
                await log.ainfo("startup.ibkr_connected")
            except Exception:
                await log.aexception("startup.ibkr_failed")

        if CCXT_API_KEY and os.getenv("ENABLE_CCXT", ""):
            try:
                await ccxt_conn.connect(
                    exchange_name=CCXT_EXCHANGE,
                    api_key=CCXT_API_KEY,
                    secret=CCXT_SECRET,
                )
                await log.ainfo("startup.ccxt_connected")
            except Exception:
                await log.aexception("startup.ccxt_failed")

    trade_task = asyncio.create_task(_trade_close_consumer())
    _streaming_tasks.append(trade_task)
    feed_task = asyncio.create_task(_start_broker_feeds())
    _streaming_tasks.append(feed_task)
    await log.ainfo("startup.trade_consumer_started")
    await log.ainfo("startup.complete")

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    await log.ainfo("shutdown.begin")

    for t in _streaming_tasks:
        t.cancel()
    if _streaming_tasks:
        await asyncio.gather(*_streaming_tasks, return_exceptions=True)
    _streaming_tasks.clear()

    await mt5.disconnect()
    await ibkr.disconnect()
    await ccxt_conn.disconnect()
    data_store.close()
    await redis_client.disconnect()

    await log.ainfo("shutdown.complete")


# ── FastAPI app ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Nexus Data Service",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check() -> dict[str, Any]:
    redis_ok = await redis_client.health_check()
    mt5_ok = mt5.connected
    data_mode = "REAL_MT5" if mt5_ok and not SYNTHETIC_FALLBACK else (
        "SYNTHETIC_ALLOWED" if SYNTHETIC_FALLBACK else "REAL_MT5_REQUIRED"
    )
    duckdb_ok = data_store.is_open
    # Startup gate: DuckDB writer must be up; MT5 may still be connecting.
    if redis_ok and duckdb_ok and mt5_ok:
        status = "healthy"
    elif redis_ok and duckdb_ok:
        status = "degraded"
    else:
        status = "unhealthy"
    return {
        "service": "nexus-data",
        "status": status,
        "ready": redis_ok and duckdb_ok,
        "data_mode": data_mode,
        "synthetic_fallback": SYNTHETIC_FALLBACK,
        "redis": redis_ok,
        "duckdb": data_store.is_open,
        "mt5": mt5_ok,
        "ibkr": ibkr.connected,
        "ccxt": ccxt_conn.connected,
    }


# ── Tick endpoint ────────────────────────────────────────────────────────────

@app.get("/api/data/ticks/{symbol}", response_model=list[Tick])
async def get_ticks(
    symbol: str,
    count: int = Query(default=50, ge=1, le=1000),
    source: str = Query(default="cache", regex="^(cache|db)$"),
) -> list[Tick]:
    """Return the most recent ticks for *symbol*."""
    symbol = symbol.upper().replace("/", "")
    if source == "cache":
        ticks = await tick_cache.get_tick_history(symbol, count)
        return ticks or []

    rows = await data_store.query_latest_ticks(symbol, count)
    if rows:
        return rows

    if DEFAULT_BROKER == "mt5" and mt5.connected:
        try:
            live = await mt5.get_tick(symbol)
            return [live]
        except Exception:
            await log.aexception("ticks_mt5_fallback_failed", symbol=symbol)

    if SYNTHETIC_FALLBACK:
        await log.awarning("ticks.synthetic_fallback", symbol=symbol)
        return [_synthetic_tick(symbol)]

    raise HTTPException(404, f"No ticks found for {symbol}")


# ── Order flow (derived from M1 bars + latest tick) ──────────────────────────

@app.get("/api/flow/{symbol}")
@app.get("/api/data/flow/{symbol}")
async def get_order_flow(symbol: str, bars: int = Query(default=20, ge=5, le=100)) -> dict[str, Any]:
    """Estimate bid/ask pressure from recent M1 candle direction and volume."""
    symbol = symbol.upper().replace("/", "")
    bars_data = await data_store.query_ohlcv(symbol, "M1", limit=bars)
    if not bars_data:
        try:
            bars_data = await mt5.get_ohlcv(symbol, "M1", bars)
        except Exception:
            bars_data = []

    bid_volume = 0.0
    ask_volume = 0.0
    spreads: list[float] = []

    for bar in bars_data:
        vol = float(getattr(bar, "volume", 0) or (bar.get("volume", 0) if isinstance(bar, dict) else 0))
        o = float(getattr(bar, "open", 0) or (bar.get("open", 0) if isinstance(bar, dict) else 0))
        c = float(getattr(bar, "close", 0) or (bar.get("close", 0) if isinstance(bar, dict) else 0))
        if vol <= 0:
            continue
        if c >= o:
            bid_volume += vol
        else:
            ask_volume += vol

    ticks = await tick_cache.get_tick_history(symbol, 5)
    if not ticks:
        rows = await data_store.query_latest_ticks(symbol, 5)
        ticks = rows or []
    for t in ticks:
        sp = float(getattr(t, "spread", 0) or (t.get("spread", 0) if isinstance(t, dict) else 0))
        if sp > 0:
            spreads.append(sp)

    spread_current = spreads[-1] if spreads else 0.0
    spread_avg = sum(spreads) / len(spreads) if spreads else spread_current or 1.0
    total = bid_volume + ask_volume
    delta = bid_volume - ask_volume
    imbalance = delta / total if total else 0.0

    absorption = "none"
    if total > 0 and abs(imbalance) < 0.1:
        absorption = "balanced"
    elif imbalance > 0.35:
        absorption = "buy_absorption"
    elif imbalance < -0.35:
        absorption = "sell_absorption"

    return {
        "symbol": symbol,
        "bid_volume": round(bid_volume, 2),
        "ask_volume": round(ask_volume, 2),
        "delta": round(delta, 2),
        "absorption": absorption,
        "spread": round(spread_current, 5),
        "spread_avg": round(spread_avg, 5),
        "imbalance": round(imbalance, 4),
        "large_orders": [],
        "iceberg_detected": False,
        "bars_analyzed": len(bars_data),
    }


# ── OHLCV endpoint ───────────────────────────────────────────────────────────

@app.get("/api/data/ohlcv/{symbol}/{timeframe}", response_model=list[OHLCV])
async def get_ohlcv(
    symbol: str,
    timeframe: str,
    limit: int = Query(default=500, ge=1, le=5000),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
) -> list[OHLCV]:
    """Return OHLCV bars for *symbol* / *timeframe*."""
    symbol = symbol.upper().replace("/", "")
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise HTTPException(
            400,
            f"Unsupported timeframe '{timeframe}'. Use one of {SUPPORTED_TIMEFRAMES}",
        )
    rows = await data_store.query_ohlcv(
        symbol=symbol, timeframe=timeframe, start=start, end=end, limit=limit,
    )
    if rows:
        return rows

    # DuckDB ``ohlcv`` is not filled from tick ingest — pull live bars from MT5 when connected.
    if DEFAULT_BROKER == "mt5" and mt5.connected and not start and not end:
        try:
            return await mt5.get_ohlcv(symbol, timeframe, limit)
        except Exception:
            await log.aexception("ohlcv_mt5_fallback_failed", symbol=symbol, tf=timeframe)

    if SYNTHETIC_FALLBACK and not start and not end:
        await log.awarning("ohlcv.synthetic_fallback", symbol=symbol, tf=timeframe)
        return _synthetic_ohlcv(symbol, timeframe, limit)

    raise HTTPException(404, f"No OHLCV data for {symbol}/{timeframe}")


# ── Subscribe endpoint ───────────────────────────────────────────────────────

@app.get("/api/data/performance")
async def get_performance() -> dict:
    return await data_store.get_performance_metrics()


@app.get("/api/data/performance/equity-curve")
async def get_equity_curve(limit: int = Query(200, ge=1, le=2000)) -> list:
    return await data_store.get_pnl_curve(limit=limit)


@app.get("/api/data/performance/daily")
async def get_daily_pnl(since: str | None = Query(None)) -> list:
    return await data_store.get_daily_pnl(since=since)


@app.get("/api/data/performance/by-session")
async def get_perf_by_session() -> list:
    return await data_store.get_grouped_performance("session")


@app.get("/api/data/performance/by-symbol")
async def get_perf_by_symbol() -> list:
    return await data_store.get_grouped_performance("symbol")


@app.get("/api/data/trades")
async def get_trades_history(
    symbol: str | None = Query(None),
    session: str | None = Query(None),
    since: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=5000),
) -> dict:
    """Paginated trade history from DuckDB — served by the owner of the write lock."""
    return await data_store.query_trades(
        symbol=symbol,
        session=session,
        since=since,
        page=page,
        page_size=page_size,
    )


class SubscribeRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1)
    broker: str = Field(default="mt5", pattern="^(mt5|ibkr|ccxt)$")


@app.post("/api/data/subscribe")
async def subscribe(req: SubscribeRequest) -> dict[str, Any]:
    """Start streaming ticks for additional symbols at runtime."""
    broker_map = {"mt5": mt5, "ibkr": ibkr, "ccxt": ccxt_conn}
    connector = broker_map.get(req.broker)
    if connector is None or not connector.connected:
        raise HTTPException(
            503, f"Broker '{req.broker}' is not connected",
        )

    task = asyncio.create_task(
        connector.stream_ticks(req.symbols, _on_tick),
    )
    _streaming_tasks.append(task)

    await log.ainfo(
        "subscribe.started", broker=req.broker, symbols=req.symbols,
    )
    return {"status": "subscribed", "symbols": req.symbols, "broker": req.broker}
