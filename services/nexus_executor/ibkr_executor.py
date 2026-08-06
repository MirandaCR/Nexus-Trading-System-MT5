"""
Nexus Executor — Interactive Brokers implementation via ib_insync.

Supports market, limit, and stop orders with bracket orders for SL/TP.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import structlog

from .broker_adapter import (
    AccountInfo,
    BrokerAdapter,
    OrderRequest,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
)

log = structlog.get_logger(__name__)


class IBKRExecutor(BrokerAdapter):
    """Interactive Brokers execution adapter using ib_insync."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        client_id: int | None = None,
    ) -> None:
        self._host = host or os.getenv("IBKR_HOST", "127.0.0.1")
        self._port = port or int(os.getenv("IBKR_PORT", "7497"))
        self._client_id = client_id or int(os.getenv("IBKR_CLIENT_ID", "1"))
        self._ib: Any = None
        self._connected = False

    def _get_ib(self) -> Any:
        if self._ib is None:
            try:
                from ib_insync import IB
                self._ib = IB()
            except ImportError:
                raise RuntimeError("ib_insync package not installed")
        return self._ib

    async def connect(self) -> bool:
        ib = self._get_ib()
        try:
            await asyncio.to_thread(
                ib.connect, self._host, self._port, clientId=self._client_id,
            )
            self._connected = ib.isConnected()
            log.info("ibkr.connected", host=self._host, port=self._port)
            return self._connected
        except Exception as exc:
            log.error("ibkr.connect_failed", error=str(exc))
            return False

    async def disconnect(self) -> None:
        if self._ib and self._connected:
            await asyncio.to_thread(self._ib.disconnect)
            self._connected = False
            log.info("ibkr.disconnected")

    def _make_contract(self, symbol: str) -> Any:
        from ib_insync import Forex
        sym = symbol.upper().replace("/", "")
        if len(sym) == 6:
            return Forex(sym[:3] + sym[3:])
        from ib_insync import Stock
        return Stock(sym, "SMART", "USD")

    async def send_order(self, order: OrderRequest) -> OrderResponse:
        ib = self._get_ib()
        from ib_insync import LimitOrder, MarketOrder, StopOrder

        contract = self._make_contract(order.symbol)

        action = "BUY" if order.side == OrderSide.BUY else "SELL"

        if order.order_type == OrderType.MARKET:
            ib_order = MarketOrder(action, order.size)
        elif order.order_type == OrderType.LIMIT:
            ib_order = LimitOrder(action, order.size, order.price)
        elif order.order_type == OrderType.STOP:
            ib_order = StopOrder(action, order.size, order.price)
        else:
            return OrderResponse(
                symbol=order.symbol,
                status=OrderStatus.ERROR,
                error=f"Unsupported order type: {order.order_type}",
                broker="IBKR",
            )

        try:
            trade = await asyncio.to_thread(ib.placeOrder, contract, ib_order)

            await asyncio.sleep(2)
            await asyncio.to_thread(ib.sleep, 0.1)

            fill_price = 0.0
            filled_size = 0.0
            if trade.fills:
                fill_price = trade.fills[-1].execution.price
                filled_size = sum(f.execution.shares for f in trade.fills)

            if trade.orderStatus.status in ("Filled",):
                status = OrderStatus.FILLED
            elif trade.orderStatus.status in ("Submitted", "PreSubmitted"):
                status = OrderStatus.PENDING
            elif trade.orderStatus.status == "Cancelled":
                status = OrderStatus.CANCELLED
            else:
                status = OrderStatus.PENDING

            order_id = str(trade.order.orderId)

            if order.sl or order.tp:
                await self._place_bracket_legs(ib, contract, order, action, order_id)

            log.info(
                "ibkr.order.placed",
                order_id=order_id,
                status=status,
                fill_price=fill_price,
            )

            return OrderResponse(
                order_id=order_id,
                symbol=order.symbol,
                side=action,
                order_type=order.order_type.value,
                requested_price=order.price,
                fill_price=fill_price,
                size=filled_size or order.size,
                sl=order.sl,
                tp=order.tp,
                status=status,
                broker="IBKR",
            )

        except Exception as exc:
            log.error("ibkr.order.failed", error=str(exc))
            return OrderResponse(
                symbol=order.symbol,
                status=OrderStatus.ERROR,
                error=str(exc),
                broker="IBKR",
            )

    async def _place_bracket_legs(
        self,
        ib: Any,
        contract: Any,
        order: OrderRequest,
        parent_action: str,
        parent_id: str,
    ) -> None:
        from ib_insync import LimitOrder, StopOrder

        child_action = "SELL" if parent_action == "BUY" else "BUY"

        try:
            if order.tp:
                tp_order = LimitOrder(child_action, order.size, order.tp)
                tp_order.parentId = int(parent_id)
                await asyncio.to_thread(ib.placeOrder, contract, tp_order)

            if order.sl:
                sl_order = StopOrder(child_action, order.size, order.sl)
                sl_order.parentId = int(parent_id)
                await asyncio.to_thread(ib.placeOrder, contract, sl_order)
        except Exception as exc:
            log.warning("ibkr.bracket_legs.error", error=str(exc))

    async def modify_order(
        self,
        position_id: str,
        *,
        sl: float | None = None,
        tp: float | None = None,
    ) -> OrderResponse:
        log.warning("ibkr.modify_order", msg="Modify via bracket order replacement")
        return OrderResponse(
            order_id=position_id,
            status=OrderStatus.ERROR,
            error="IBKR modify requires cancelling and replacing bracket legs",
            broker="IBKR",
        )

    async def close_position(
        self,
        position_id: str,
        *,
        size: float | None = None,
    ) -> OrderResponse:
        ib = self._get_ib()
        from ib_insync import MarketOrder

        try:
            positions = await asyncio.to_thread(ib.positions)
            target = None
            for pos in positions:
                if str(pos.contract.conId) == position_id or str(pos.account) == position_id:
                    target = pos
                    break

            if target is None:
                return OrderResponse(
                    order_id=position_id,
                    status=OrderStatus.ERROR,
                    error=f"Position {position_id} not found",
                    broker="IBKR",
                )

            close_size = size or abs(target.position)
            position_side = "BUY" if target.position > 0 else "SELL"
            action = "SELL" if target.position > 0 else "BUY"
            close_order = MarketOrder(action, close_size)
            trade = await asyncio.to_thread(ib.placeOrder, target.contract, close_order)

            entry_price = float(target.avgCost) if hasattr(target, "avgCost") else 0.0

            return OrderResponse(
                order_id=str(trade.order.orderId),
                symbol=target.contract.symbol,
                side=position_side,
                requested_price=entry_price,
                size=close_size,
                status=OrderStatus.FILLED,
                broker="IBKR",
                raw_response={
                    "entry_price": entry_price,
                    "position_side": position_side,
                },
            )
        except Exception as exc:
            log.error("ibkr.close.failed", error=str(exc))
            return OrderResponse(
                order_id=position_id,
                status=OrderStatus.ERROR,
                error=str(exc),
                broker="IBKR",
            )

    async def get_positions(self) -> list[dict[str, Any]]:
        ib = self._get_ib()
        try:
            positions = await asyncio.to_thread(ib.positions)
            return [
                {
                    "id": str(pos.contract.conId),
                    "symbol": pos.contract.symbol + pos.contract.currency,
                    "side": "BUY" if pos.position > 0 else "SELL",
                    "size": abs(pos.position),
                    "entry_price": pos.avgCost,
                    "pnl_usd": getattr(pos, "unrealizedPNL", 0),
                }
                for pos in positions
                if pos.position != 0
            ]
        except Exception as exc:
            log.error("ibkr.get_positions.failed", error=str(exc))
            return []

    async def get_account_info(self) -> AccountInfo:
        ib = self._get_ib()
        try:
            summary = await asyncio.to_thread(ib.accountSummary)
            values: dict[str, float] = {}
            for item in summary:
                if item.tag in ("NetLiquidation", "TotalCashValue", "BuyingPower", "GrossPositionValue"):
                    try:
                        values[item.tag] = float(item.value)
                    except (ValueError, TypeError):
                        pass

            return AccountInfo(
                balance=values.get("TotalCashValue", 0),
                equity=values.get("NetLiquidation", 0),
                free_margin=values.get("BuyingPower", 0),
                broker="IBKR",
            )
        except Exception as exc:
            log.error("ibkr.account_info.failed", error=str(exc))
            return AccountInfo(broker="IBKR")
