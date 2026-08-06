"""
Nexus Trader — DuckDB analytical store.

Provides a thin async-friendly wrapper around DuckDB for:
  * tick / OHLCV / feature / trade / agent-log persistence
  * analytical queries (performance, PnL curves, etc.)

DuckDB itself is not async, so blocking calls are delegated to a thread-pool
via ``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_DB_PATH = os.getenv(
    "DUCKDB_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "nexus.duckdb"),
)

# ── DDL ──────────────────────────────────────────────────────────────────────

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS ticks (
    symbol        VARCHAR   NOT NULL,
    bid           DOUBLE    NOT NULL,
    ask           DOUBLE    NOT NULL,
    spread        DOUBLE    NOT NULL,
    ts            TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS ohlcv (
    symbol    VARCHAR   NOT NULL,
    timeframe VARCHAR   NOT NULL,
    open      DOUBLE    NOT NULL,
    high      DOUBLE    NOT NULL,
    low       DOUBLE    NOT NULL,
    close     DOUBLE    NOT NULL,
    volume    DOUBLE    NOT NULL DEFAULT 0,
    ts        TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS features (
    symbol    VARCHAR   NOT NULL,
    timeframe VARCHAR   NOT NULL,
    features  JSON      NOT NULL,
    ts        TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id          VARCHAR PRIMARY KEY,
    symbol      VARCHAR   NOT NULL,
    side        VARCHAR   NOT NULL,
    entry_price DOUBLE    NOT NULL,
    exit_price  DOUBLE    NOT NULL,
    pnl_pips    DOUBLE    NOT NULL DEFAULT 0,
    pnl_usd     DOUBLE    NOT NULL DEFAULT 0,
    rr_ratio    DOUBLE    NOT NULL DEFAULT 0,
    duration_s  DOUBLE    NOT NULL DEFAULT 0,
    session     VARCHAR,
    conviction  DOUBLE    NOT NULL DEFAULT 0,
    opened_at   TIMESTAMP NOT NULL,
    closed_at   TIMESTAMP NOT NULL
);

ALTER TABLE trades ADD COLUMN IF NOT EXISTS strategy VARCHAR DEFAULT '';

CREATE TABLE IF NOT EXISTS positions (
    id           VARCHAR PRIMARY KEY,
    symbol       VARCHAR   NOT NULL,
    side         VARCHAR   NOT NULL,
    entry_price  DOUBLE    NOT NULL,
    current_price DOUBLE   NOT NULL DEFAULT 0,
    sl           DOUBLE    NOT NULL DEFAULT 0,
    tp1          DOUBLE    NOT NULL DEFAULT 0,
    tp2          DOUBLE,
    tp3          DOUBLE,
    size         DOUBLE    NOT NULL DEFAULT 0.01,
    pnl_pips     DOUBLE    NOT NULL DEFAULT 0,
    pnl_usd      DOUBLE    NOT NULL DEFAULT 0,
    status       VARCHAR   NOT NULL DEFAULT 'open',
    opened_at    TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_logs (
    agent_id   VARCHAR   NOT NULL,
    agent_name VARCHAR   NOT NULL,
    msg_type   VARCHAR   NOT NULL,
    content    TEXT      NOT NULL,
    metadata   JSON,
    ts         TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS performance (
    snapshot_ts    TIMESTAMP NOT NULL,
    total_pnl      DOUBLE  NOT NULL DEFAULT 0,
    win_rate       DOUBLE  NOT NULL DEFAULT 0,
    profit_factor  DOUBLE  NOT NULL DEFAULT 0,
    sharpe_ratio   DOUBLE  NOT NULL DEFAULT 0,
    max_drawdown   DOUBLE  NOT NULL DEFAULT 0,
    total_trades   INTEGER NOT NULL DEFAULT 0,
    avg_win        DOUBLE  NOT NULL DEFAULT 0,
    avg_loss       DOUBLE  NOT NULL DEFAULT 0
);
"""

_READ_ONLY_BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id          VARCHAR PRIMARY KEY,
    symbol      VARCHAR   NOT NULL,
    side        VARCHAR   NOT NULL,
    entry_price DOUBLE    NOT NULL,
    exit_price  DOUBLE    NOT NULL,
    pnl_pips    DOUBLE    NOT NULL DEFAULT 0,
    pnl_usd     DOUBLE    NOT NULL DEFAULT 0,
    rr_ratio    DOUBLE    NOT NULL DEFAULT 0,
    duration_s  DOUBLE    NOT NULL DEFAULT 0,
    session     VARCHAR,
    conviction  DOUBLE    NOT NULL DEFAULT 0,
    opened_at   TIMESTAMP NOT NULL,
    closed_at   TIMESTAMP NOT NULL
);

ALTER TABLE trades ADD COLUMN IF NOT EXISTS strategy VARCHAR DEFAULT '';

CREATE TABLE IF NOT EXISTS positions (
    id           VARCHAR PRIMARY KEY,
    symbol       VARCHAR   NOT NULL,
    side         VARCHAR   NOT NULL,
    entry_price  DOUBLE    NOT NULL,
    current_price DOUBLE   NOT NULL DEFAULT 0,
    sl           DOUBLE    NOT NULL DEFAULT 0,
    tp1          DOUBLE    NOT NULL DEFAULT 0,
    tp2          DOUBLE,
    tp3          DOUBLE,
    size         DOUBLE    NOT NULL DEFAULT 0.01,
    pnl_pips     DOUBLE    NOT NULL DEFAULT 0,
    pnl_usd      DOUBLE    NOT NULL DEFAULT 0,
    status       VARCHAR   NOT NULL DEFAULT 'open',
    opened_at    TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_logs (
    agent_id   VARCHAR   NOT NULL,
    agent_name VARCHAR   NOT NULL,
    msg_type   VARCHAR   NOT NULL,
    content    TEXT      NOT NULL,
    metadata   JSON,
    ts         TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS performance (
    snapshot_ts    TIMESTAMP NOT NULL,
    total_pnl      DOUBLE  NOT NULL DEFAULT 0,
    win_rate       DOUBLE  NOT NULL DEFAULT 0,
    profit_factor  DOUBLE  NOT NULL DEFAULT 0,
    sharpe_ratio   DOUBLE  NOT NULL DEFAULT 0,
    max_drawdown   DOUBLE  NOT NULL DEFAULT 0,
    total_trades   INTEGER NOT NULL DEFAULT 0,
    avg_win        DOUBLE  NOT NULL DEFAULT 0,
    avg_loss       DOUBLE  NOT NULL DEFAULT 0
);
"""


class DuckDBClient:
    """Thread-safe DuckDB wrapper with async helpers."""

    def __init__(self, db_path: str | None = None, *, read_only: bool = False) -> None:
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._read_only = read_only
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._lock = asyncio.Lock()

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def init_db(self, *, retries: int = 10, delay: float = 3.0) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        for attempt in range(1, retries + 1):
            try:
                if self._read_only:
                    # Skip the write-bootstrap on read-only: nexus-data owns the
                    # file and has the write lock (especially on Windows).
                    # Open directly read-only — tables already exist if nexus-data
                    # started first (which run.py guarantees via health-gate).
                    self._conn = await asyncio.to_thread(
                        duckdb.connect, self._db_path, read_only=True,
                    )
                else:
                    self._conn = await asyncio.to_thread(duckdb.connect, self._db_path)
                    await asyncio.to_thread(self._conn.execute, _INIT_SQL)
                logger.info(
                    "duckdb.initialised",
                    path=self._db_path,
                    read_only=self._read_only,
                )
                return
            except Exception as exc:
                logger.warning(
                    "duckdb.init_retry",
                    attempt=attempt,
                    max=retries,
                    error=str(exc),
                )
                if attempt < retries:
                    await asyncio.sleep(delay)
        logger.error("duckdb.init_failed_all_retries", path=self._db_path)

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None
            logger.info("duckdb.closed")

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            raise RuntimeError("DuckDBClient not initialised — call init_db() first")
        return self._conn

    # ── Generic Query ────────────────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        return self._conn is not None

    async def query(
        self,
        sql: str,
        params: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        if self._conn is None:
            return []
        try:
            async with self._lock:
                result = await asyncio.to_thread(
                    self._execute, sql, params,
                )
            return result
        except Exception as exc:
            # Gracefully handle missing tables, schema mismatches, etc.
            logger.warning("duckdb.query_error", sql=sql[:120], error=str(exc))
            return []

    def _execute(
        self,
        sql: str,
        params: list[Any] | None,
    ) -> list[dict[str, Any]]:
        cursor = self.conn.execute(sql, params or [])
        if cursor.description is None:
            return []
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    async def execute(self, sql: str, params: list[Any] | None = None) -> None:
        """Execute a statement without returning results."""
        if self._conn is None:
            return
        async with self._lock:
            await asyncio.to_thread(self.conn.execute, sql, params or [])

    # ── Tick ─────────────────────────────────────────────────────────────

    async def insert_tick(
        self,
        symbol: str,
        bid: float,
        ask: float,
        spread: float,
        ts: datetime,
    ) -> None:
        await self.execute(
            "INSERT INTO ticks (symbol, bid, ask, spread, ts) VALUES (?, ?, ?, ?, ?)",
            [symbol, bid, ask, spread, ts],
        )

    # ── OHLCV ────────────────────────────────────────────────────────────

    async def insert_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        o: float,
        h: float,
        l: float,
        c: float,
        v: float,
        ts: datetime,
    ) -> None:
        await self.execute(
            "INSERT INTO ohlcv (symbol, timeframe, open, high, low, close, volume, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [symbol, timeframe, o, h, l, c, v, ts],
        )

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        return await self.query(
            "SELECT * FROM ohlcv WHERE symbol = ? AND timeframe = ? "
            "ORDER BY ts DESC LIMIT ?",
            [symbol, timeframe, limit],
        )

    # ── Trade ────────────────────────────────────────────────────────────

    async def insert_trade(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        pnl_pips: float,
        pnl_usd: float,
        rr_ratio: float,
        duration_s: float,
        session: str,
        conviction: float,
        opened_at: datetime,
        closed_at: datetime,
        strategy: str = "",
    ) -> None:
        await self.execute(
            "INSERT INTO trades "
            "(id, symbol, side, entry_price, exit_price, pnl_pips, pnl_usd, "
            "rr_ratio, duration_s, session, conviction, opened_at, closed_at, strategy) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                trade_id, symbol, side, entry_price, exit_price,
                pnl_pips, pnl_usd, rr_ratio, duration_s,
                session, conviction, opened_at, closed_at, strategy,
            ],
        )

    async def get_trades(
        self,
        *,
        symbol: str | None = None,
        session: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)
        if session:
            clauses.append("session = ?")
            params.append(session)
        if since:
            clauses.append("closed_at >= ?")
            params.append(since)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return await self.query(
            f"SELECT * FROM trades {where} ORDER BY closed_at DESC LIMIT ?",
            [*params, limit],
        )

    # ── Agent Logs ───────────────────────────────────────────────────────

    async def insert_agent_log(
        self,
        agent_id: str,
        agent_name: str,
        msg_type: str,
        content: str,
        metadata: dict[str, Any] | None,
        ts: datetime,
    ) -> None:
        import json as _json

        await self.execute(
            "INSERT INTO agent_logs VALUES (?, ?, ?, ?, ?, ?)",
            [agent_id, agent_name, msg_type, content, _json.dumps(metadata or {}), ts],
        )

    # ── Performance ──────────────────────────────────────────────────────

    async def get_performance_metrics(self) -> dict[str, Any]:
        rows = await self.query(
            """
            SELECT
                COALESCE(SUM(pnl_usd), 0)                        AS total_pnl,
                COUNT(*)                                          AS total_trades,
                COALESCE(AVG(CASE WHEN pnl_usd > 0
                                  THEN pnl_usd END), 0)          AS avg_win,
                COALESCE(AVG(CASE WHEN pnl_usd <= 0
                                  THEN pnl_usd END), 0)          AS avg_loss,
                CASE WHEN COUNT(*) > 0
                     THEN ROUND(
                         SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END)
                         * 100.0 / COUNT(*), 2)
                     ELSE 0 END                                   AS win_rate,
                CASE WHEN ABS(COALESCE(SUM(CASE WHEN pnl_usd <= 0
                                           THEN pnl_usd END), 1)) > 0
                     THEN ROUND(
                         COALESCE(SUM(CASE WHEN pnl_usd > 0
                                      THEN pnl_usd END), 0)
                         / ABS(COALESCE(SUM(CASE WHEN pnl_usd <= 0
                                            THEN pnl_usd END), 1)), 2)
                     ELSE 0 END                                   AS profit_factor
            FROM trades
            """
        )
        return rows[0] if rows else {}

    async def get_pnl_curve(self, limit: int = 200) -> list[dict[str, Any]]:
        """Last *limit* closed trades by time, chronological order, with correct running P&L."""
        return await self.query(
            """
            WITH recent AS (
                SELECT closed_at, pnl_usd
                FROM trades
                ORDER BY closed_at DESC
                LIMIT ?
            )
            SELECT
                closed_at,
                pnl_usd,
                SUM(pnl_usd) OVER (
                    ORDER BY closed_at ASC
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS cumulative_pnl
            FROM recent
            ORDER BY closed_at ASC
            """,
            [limit],
        )
