"""
Nexus Monitor — FastAPI service (port 8008).

Central monitoring hub with background tasks for:
  - Health checking all services every 30 seconds
  - Exporting Prometheus metrics
  - Telegram alert dispatch
  - Performance analytics
"""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
from shared.http_client import new_client
import structlog
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from shared.config_loader import ConfigLoader
from shared.constants import CACHE_HEALTH_PREFIX, SERVICE_PORTS, ServiceName
from shared.events import SystemHealthEvent, TradeCloseEvent
from shared.models import SystemHealth
from shared.redis_client import RedisClient

from .metrics_exporter import MetricsExporter
from .performance import PerformanceAnalyzer
from .telegram_bot import TelegramNotifier

from shared import setup_logger as _setup_logger
_setup_logger("nexus-monitor")
log = structlog.get_logger(__name__)

# ── Globals ───────────────────────────────────────────────────────────────────

redis = RedisClient()
config = ConfigLoader()
metrics = MetricsExporter()
performance = PerformanceAnalyzer()
telegram = TelegramNotifier(
    bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
    chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
)

SERVICE_NAME = os.getenv("NEXUS_SERVICE_NAME", ServiceName.MONITOR)
SERVICE_PORT = int(os.getenv("NEXUS_SERVICE_PORT", "8008"))

_HEALTH_CHECK_INTERVAL = 30
_service_statuses: dict[str, dict[str, Any]] = {}
_http_client: httpx.AsyncClient | None = None

_SERVICES_TO_CHECK = [
    ("nexus-data", SERVICE_PORTS.get("nexus-data", 8001)),
    ("nexus-features", SERVICE_PORTS.get("nexus-features", 8002)),
    ("nexus-ml", SERVICE_PORTS.get("nexus-ml", 8003)),
    ("nexus-agents", SERVICE_PORTS.get("nexus-agents", 8004)),
    ("nexus-news", SERVICE_PORTS.get("nexus-news", 8005)),
    ("nexus-risk", SERVICE_PORTS.get("nexus-risk", 8006)),
    ("nexus-executor", SERVICE_PORTS.get("nexus-executor", 8007)),
    ("nexus-api", SERVICE_PORTS.get("nexus-api", 8000)),
]


# ── Request / Response Models ─────────────────────────────────────────────────

class AlertRequest(BaseModel):
    type: str = "error"
    service: str = ""
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    services: dict[str, Any]
    overall: str
    healthy_count: int
    total_count: int
    last_check: str


# ── Background: Health Check All Services ─────────────────────────────────────

async def _health_check_loop() -> None:
    global _http_client
    _http_client = new_client(timeout=5.0)
    log.info("monitor.health_check_loop.started", interval=_HEALTH_CHECK_INTERVAL)

    while True:
        try:
            await _check_all_services()
        except Exception:
            log.exception("monitor.health_check_loop.error")
        await asyncio.sleep(_HEALTH_CHECK_INTERVAL)


async def _check_all_services() -> None:
    for name, port in _SERVICES_TO_CHECK:
        status = await _check_service(name, port)
        _service_statuses[name] = status

        health = SystemHealth(
            service_name=name,
            status=status["status"],
            latency_ms=status.get("latency_ms", 0),
        )
        await redis.cache_set(
            f"{CACHE_HEALTH_PREFIX}{name}",
            health.model_dump(mode="json"),
            ttl_sec=60,
            prefix=False,
        )
        metrics.update_service_metrics({
            "service_name": name,
            "latency_ms": status.get("latency_ms", 0),
        })

        if status["status"] == "unhealthy":
            prev = _service_statuses.get(name, {})
            if prev.get("status") != "unhealthy":
                await telegram.alert_error(name, status.get("error", "Service unreachable"))

    healthy = sum(1 for s in _service_statuses.values() if s["status"] == "healthy")
    log.info(
        "monitor.health_check_complete",
        healthy=healthy,
        total=len(_SERVICES_TO_CHECK),
    )


async def _check_service(name: str, port: int) -> dict[str, Any]:
    if _http_client is None:
        return {"status": "unknown", "latency_ms": 0, "error": "HTTP client not ready"}

    url = f"http://{name}:{port}/health"
    fallback_url = f"http://localhost:{port}/health"
    start = time.monotonic()

    for attempt_url in (url, fallback_url):
        try:
            resp = await _http_client.get(attempt_url)
            latency = (time.monotonic() - start) * 1000
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "status": data.get("status", "healthy"),
                    "latency_ms": round(latency, 1),
                    "details": data,
                    "last_check": datetime.now(timezone.utc).isoformat(),
                }
        except (httpx.ConnectError, httpx.ReadTimeout):
            continue
        except Exception:
            continue

    latency = (time.monotonic() - start) * 1000
    return {
        "status": "unhealthy",
        "latency_ms": round(latency, 1),
        "error": "Connection refused or timeout",
        "last_check": datetime.now(timezone.utc).isoformat(),
    }


# ── Background: Metrics Export ────────────────────────────────────────────────

async def _metrics_export_loop() -> None:
    log.info("monitor.metrics_export_loop.started")
    while True:
        try:
            await redis.cache_set(
                "monitor:metrics",
                metrics.get_metrics_dict(),
                ttl_sec=60,
            )
        except Exception:
            log.exception("monitor.metrics_export.error")
        await asyncio.sleep(15)


# ── Background: Trade Close Listener ─────────────────────────────────────────

async def _trade_close_listener() -> None:
    from shared.constants import RedisStream

    log.info("monitor.trade_close_listener.started")
    async for event in redis.subscribe_stream(
        RedisStream.TRADE_CLOSE,
        "nexus-monitor-group",
        "nexus-monitor-1",
    ):
        try:
            trade_data = {
                "symbol": getattr(event, "symbol", ""),
                "side": getattr(event, "side", ""),
                "pnl_pips": float(getattr(event, "pnl_pips", 0)),
                "pnl_usd": float(getattr(event, "pnl_usd", 0)),
                "rr_ratio": float(getattr(event, "rr_ratio", 0)),
            }
            metrics.update_trade_metrics(trade_data)
            await telegram.alert_trade_closed(trade_data)
        except Exception:
            log.exception("monitor.trade_close_listener.error")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis.connect()
    log.info("monitor.service.started", port=SERVICE_PORT)

    health_task = asyncio.create_task(_health_check_loop())
    metrics_task = asyncio.create_task(_metrics_export_loop())
    trade_task = asyncio.create_task(_trade_close_listener())

    try:
        yield
    finally:
        for task in (health_task, metrics_task, trade_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if _http_client:
            await _http_client.aclose()
        await redis.disconnect()
        log.info("monitor.service.stopped")


# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Nexus Monitor",
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    redis_ok = await redis.health_check()
    return {
        "service": SERVICE_NAME,
        "status": "healthy" if redis_ok else "degraded",
        "redis": redis_ok,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/monitor/health", response_model=HealthResponse)
async def get_health_status():
    healthy = sum(1 for s in _service_statuses.values() if s.get("status") == "healthy")
    total = len(_SERVICES_TO_CHECK)
    overall = "healthy" if healthy == total else "degraded" if healthy > 0 else "critical"

    return HealthResponse(
        services=_service_statuses,
        overall=overall,
        healthy_count=healthy,
        total_count=total,
        last_check=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/api/monitor/metrics")
async def get_metrics(format: str = "json"):
    if format == "prometheus":
        return Response(
            content=metrics.export_metrics(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
    return metrics.get_metrics_dict()


@app.get("/metrics")
async def prometheus_metrics():
    """Standard Prometheus scrape endpoint."""
    return Response(
        content=metrics.export_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.post("/api/monitor/alert")
async def send_alert(req: AlertRequest):
    try:
        if req.type == "error":
            sent = await telegram.alert_error(req.service, req.message)
        elif req.type == "kill_switch":
            sent = await telegram.alert_kill_switch(req.message)
        elif req.type == "daily_summary":
            sent = await telegram.alert_daily_summary(req.details)
        else:
            sent = await telegram.send_message(f"*{req.type.upper()}*\n{req.message}")

        return {"sent": sent, "type": req.type}
    except Exception as exc:
        log.exception("monitor.alert.error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/monitor/performance")
async def get_performance(limit: int = 100):
    from shared.duckdb_client import DuckDBClient

    db = DuckDBClient(read_only=True)
    try:
        await db.init_db()
        trades = await db.get_trades(limit=limit)
        m = performance.calculate_metrics(trades)
        return {
            "total_pnl": m.total_pnl,
            "win_rate": m.win_rate,
            "profit_factor": m.profit_factor,
            "sharpe_ratio": m.sharpe_ratio,
            "sortino_ratio": m.sortino_ratio,
            "max_drawdown": m.max_drawdown,
            "total_trades": m.total_trades,
            "winning_trades": m.winning_trades,
            "losing_trades": m.losing_trades,
            "avg_win": m.avg_win,
            "avg_loss": m.avg_loss,
            "best_trade": m.best_trade,
            "worst_trade": m.worst_trade,
            "expectancy": m.expectancy,
            "consecutive_wins": m.consecutive_wins,
            "consecutive_losses": m.consecutive_losses,
        }
    except Exception as exc:
        log.exception("monitor.performance.error")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await db.close()
