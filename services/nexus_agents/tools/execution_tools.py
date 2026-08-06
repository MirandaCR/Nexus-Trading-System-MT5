"""
Execution tools for sending and managing orders via the broker service.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from shared.http_client import new_client
import structlog

from shared.nexus_trades import normalize_nexus_comment

logger = structlog.get_logger(__name__)

EXECUTOR_SERVICE_URL = os.getenv("NEXUS_EXECUTOR_URL", "http://localhost:8007")


def _normalise_order_payload(order_params: dict[str, Any]) -> dict[str, Any]:
    """Translate agent order fields to the executor REST contract."""
    order_type = str(order_params.get("order_type", "MARKET")).upper()
    return {
        "symbol": order_params.get("symbol", ""),
        "side": str(order_params.get("side", "BUY")).upper(),
        "order_type": order_type,
        "size": float(order_params.get("size", 0.01) or 0.01),
        "price": float(order_params.get("price", order_params.get("entry", 0.0)) or 0.0),
        "sl": float(order_params.get("sl", 0.0) or 0.0),
        "tp": float(order_params.get("tp", order_params.get("tp1", 0.0)) or 0.0),
        "slippage": int(order_params.get("slippage", 20) or 20),
        "comment": normalize_nexus_comment(
            str(order_params.get("comment", "")),
            strategy=str(order_params.get("strategy", "genai")),
        ),
        "use_smart_routing": bool(order_params.get("use_smart_routing", True)),
        "bid": float(order_params.get("bid", 0.0) or 0.0),
        "ask": float(order_params.get("ask", 0.0) or 0.0),
        "current_spread": float(order_params.get("current_spread", order_params.get("spread", 0.0)) or 0.0),
        # Signal context — the executor persists this for close-time telemetry.
        "strategy": str(order_params.get("strategy", "genai")),
        "conviction": float(order_params.get("conviction", 0.0) or 0.0),
        "session": str(order_params.get("session", "") or ""),
        "atr": float(order_params.get("atr", 0.0) or 0.0),
        "regime": str(order_params.get("regime", "") or ""),
    }


async def send_order(order_params: dict[str, Any]) -> dict[str, Any]:
    """
    Send an order to the broker via the execution service.

    Returns the broker response including order_id and status.
    """
    try:
        payload = _normalise_order_payload(order_params)
        async with new_client(timeout=10.0) as client:
            resp = await client.post(
                f"{EXECUTOR_SERVICE_URL}/api/executor/order",
                json=payload,
            )
            if resp.status_code in (200, 201):
                return resp.json()
            return {
                "status": "error",
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
    except httpx.TimeoutException:
        await logger.awarning("tools.send_order_timeout")
        return {"status": "timeout", "error": "Order submission timed out"}
    except Exception as exc:
        await logger.awarning("tools.send_order_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}


async def modify_order(order_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Modify an existing order (SL/TP/trailing adjustments)."""
    try:
        async with new_client(timeout=5.0) as client:
            resp = await client.put(
                f"{EXECUTOR_SERVICE_URL}/api/executor/position/{order_id}",
                json=params,
            )
            if resp.status_code == 200:
                return resp.json()
            return {
                "status": "error",
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
    except Exception as exc:
        await logger.awarning("tools.modify_order_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}


async def close_position(position_id: str) -> dict[str, Any]:
    """Close an open position by its ID."""
    try:
        async with new_client(timeout=10.0) as client:
            resp = await client.delete(
                f"{EXECUTOR_SERVICE_URL}/api/executor/position/{position_id}"
            )
            if resp.status_code == 200:
                return resp.json()
            return {
                "status": "error",
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
    except Exception as exc:
        await logger.awarning("tools.close_position_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}
