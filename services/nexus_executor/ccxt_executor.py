"""
Nexus Executor — Crypto exchange execution via CCXT.

Supports Binance, Bybit, and OKX for both spot and futures trading
with stop-loss / take-profit via separate stop orders.
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

_SUPPORTED_EXCHANGES = ("binance", "bybit", "okx")


class CCXTExecutor(BrokerAdapter):
    """CCXT-based crypto exchange adapter (Binance, Bybit, OKX)."""

    def __init__(
        self,
        exchange_id: str = "binance",
        api_key: str | None = None,
        secret: str | None = None,
        password: str | None = None,
        *,
        sandbox: bool = True,
        market_type: str = "spot",
    ) -> None:
        self._exchange_id = exchange_id.lower()
        self._api_key = api_key or os.getenv(f"{exchange_id.upper()}_API_KEY", "")
        self._secret = secret or os.getenv(f"{exchange_id.upper()}_SECRET", "")
        self._password = password or os.getenv(f"{exchange_id.upper()}_PASSWORD", "")
        self._sandbox = sandbox
        self._market_type = market_type
        self._exchange: Any = None
        self._connected = False

    def _create_exchange(self) -> Any:
        try:
            import ccxt.async_support as ccxt
        except ImportError:
            raise RuntimeError("ccxt package not installed")

        exchange_class = getattr(ccxt, self._exchange_id, None)
        if exchange_class is None:
            raise ValueError(f"Exchange {self._exchange_id} not supported by ccxt")

        config: dict[str, Any] = {
            "apiKey": self._api_key,
            "secret": self._secret,
            "enableRateLimit": True,
            "options": {
                "defaultType": self._market_type,
            },
        }
        if self._password:
            config["password"] = self._password

        exchange = exchange_class(config)

        if self._sandbox:
            exchange.set_sandbox_mode(True)

        return exchange

    async def connect(self) -> bool:
        try:
            self._exchange = self._create_exchange()
            await self._exchange.load_markets()
            self._connected = True
            log.info(
                "ccxt.connected",
                exchange=self._exchange_id,
                sandbox=self._sandbox,
                markets=len(self._exchange.markets),
            )
            return True
        except Exception as exc:
            log.error("ccxt.connect_failed", error=str(exc))
            return False

    async def disconnect(self) -> None:
        if self._exchange:
            await self._exchange.close()
            self._connected = False
            log.info("ccxt.disconnected", exchange=self._exchange_id)

    async def send_order(self, order: OrderRequest) -> OrderResponse:
        if not self._exchange:
            return OrderResponse(
                symbol=order.symbol,
                status=OrderStatus.ERROR,
                error="Exchange not connected",
                broker=self._exchange_id,
            )

        try:
            side = "buy" if order.side == OrderSide.BUY else "sell"

            if order.order_type == OrderType.MARKET:
                result = await self._exchange.create_order(
                    symbol=order.symbol,
                    type="market",
                    side=side,
                    amount=order.size,
                )
            elif order.order_type == OrderType.LIMIT:
                result = await self._exchange.create_order(
                    symbol=order.symbol,
                    type="limit",
                    side=side,
                    amount=order.size,
                    price=order.price,
                )
            elif order.order_type == OrderType.STOP:
                params: dict[str, Any] = {}
                if self._exchange_id == "binance":
                    params["stopPrice"] = order.price
                result = await self._exchange.create_order(
                    symbol=order.symbol,
                    type="stop" if self._exchange_id != "binance" else "STOP_LOSS_LIMIT",
                    side=side,
                    amount=order.size,
                    price=order.price,
                    params=params,
                )
            else:
                return OrderResponse(
                    symbol=order.symbol,
                    status=OrderStatus.ERROR,
                    error=f"Unsupported order type: {order.order_type}",
                    broker=self._exchange_id,
                )

            ccxt_status = result.get("status", "")
            if ccxt_status == "closed":
                status = OrderStatus.FILLED
            elif ccxt_status == "open":
                status = OrderStatus.PENDING
            elif ccxt_status == "canceled":
                status = OrderStatus.CANCELLED
            else:
                status = OrderStatus.PENDING

            order_id = str(result.get("id", ""))

            if order.sl:
                await self._place_stop_loss(order, order_id)
            if order.tp:
                await self._place_take_profit(order, order_id)

            log.info(
                "ccxt.order.placed",
                exchange=self._exchange_id,
                order_id=order_id,
                status=status,
            )

            return OrderResponse(
                order_id=order_id,
                symbol=order.symbol,
                side=side.upper(),
                order_type=order.order_type.value,
                requested_price=order.price,
                fill_price=float(result.get("average", 0) or result.get("price", 0) or 0),
                size=float(result.get("filled", 0) or order.size),
                sl=order.sl,
                tp=order.tp,
                status=status,
                broker=self._exchange_id,
                raw_response=result,
            )

        except Exception as exc:
            log.error("ccxt.order.failed", error=str(exc))
            return OrderResponse(
                symbol=order.symbol,
                status=OrderStatus.ERROR,
                error=str(exc),
                broker=self._exchange_id,
            )

    async def _place_stop_loss(self, order: OrderRequest, parent_id: str) -> None:
        try:
            sl_side = "sell" if order.side == OrderSide.BUY else "buy"
            params: dict[str, Any] = {"stopPrice": order.sl}
            if self._exchange_id == "binance":
                params["type"] = "STOP_LOSS_LIMIT"

            await self._exchange.create_order(
                symbol=order.symbol,
                type="stop_loss" if self._exchange_id != "okx" else "trigger",
                side=sl_side,
                amount=order.size,
                price=order.sl,
                params=params,
            )
        except Exception as exc:
            log.warning("ccxt.sl_order.failed", error=str(exc))

    async def _place_take_profit(self, order: OrderRequest, parent_id: str) -> None:
        try:
            tp_side = "sell" if order.side == OrderSide.BUY else "buy"
            params: dict[str, Any] = {"stopPrice": order.tp}

            await self._exchange.create_order(
                symbol=order.symbol,
                type="take_profit" if self._exchange_id != "okx" else "trigger",
                side=tp_side,
                amount=order.size,
                price=order.tp,
                params=params,
            )
        except Exception as exc:
            log.warning("ccxt.tp_order.failed", error=str(exc))

    async def modify_order(
        self,
        position_id: str,
        *,
        sl: float | None = None,
        tp: float | None = None,
    ) -> OrderResponse:
        log.warning("ccxt.modify_order", msg="Cancel and replace SL/TP orders required")
        return OrderResponse(
            order_id=position_id,
            status=OrderStatus.ERROR,
            error="CCXT modify requires cancelling and replacing stop orders",
            broker=self._exchange_id,
        )

    async def close_position(
        self,
        position_id: str,
        *,
        size: float | None = None,
    ) -> OrderResponse:
        if not self._exchange:
            return OrderResponse(
                order_id=position_id,
                status=OrderStatus.ERROR,
                error="Exchange not connected",
                broker=self._exchange_id,
            )

        try:
            order_info = await self._exchange.fetch_order(position_id)
            symbol = order_info.get("symbol", "")
            original_side = order_info.get("side", "buy")
            position_side = original_side.upper()
            close_side = "sell" if original_side == "buy" else "buy"
            close_amount = size or float(order_info.get("filled", 0))
            entry_price = float(order_info.get("average", 0) or 0)

            if close_amount <= 0:
                return OrderResponse(
                    order_id=position_id,
                    status=OrderStatus.ERROR,
                    error="No filled amount to close",
                    broker=self._exchange_id,
                )

            result = await self._exchange.create_order(
                symbol=symbol,
                type="market",
                side=close_side,
                amount=close_amount,
            )

            return OrderResponse(
                order_id=str(result.get("id", "")),
                symbol=symbol,
                side=position_side,
                requested_price=entry_price,
                fill_price=float(result.get("average", 0) or 0),
                size=close_amount,
                status=OrderStatus.FILLED,
                broker=self._exchange_id,
                raw_response={
                    "entry_price": entry_price,
                    "position_side": position_side,
                    "opened_at": order_info.get("datetime", ""),
                },
            )

        except Exception as exc:
            log.error("ccxt.close.failed", error=str(exc))
            return OrderResponse(
                order_id=position_id,
                status=OrderStatus.ERROR,
                error=str(exc),
                broker=self._exchange_id,
            )

    async def get_positions(self) -> list[dict[str, Any]]:
        if not self._exchange:
            return []

        try:
            if self._market_type == "future":
                positions = await self._exchange.fetch_positions()
                return [
                    {
                        "id": str(p.get("id", "")),
                        "symbol": p.get("symbol", ""),
                        "side": p.get("side", "").upper(),
                        "size": abs(float(p.get("contracts", 0))),
                        "entry_price": float(p.get("entryPrice", 0)),
                        "current_price": float(p.get("markPrice", 0)),
                        "pnl_usd": float(p.get("unrealizedPnl", 0)),
                        "leverage": float(p.get("leverage", 1)),
                    }
                    for p in positions
                    if float(p.get("contracts", 0)) != 0
                ]
            else:
                balance = await self._exchange.fetch_balance()
                positions: list[dict[str, Any]] = []
                for currency, amount in balance.get("total", {}).items():
                    if amount and float(amount) > 0 and currency not in ("USDT", "USD", "BUSD"):
                        positions.append({
                            "id": currency,
                            "symbol": f"{currency}/USDT",
                            "side": "BUY",
                            "size": float(amount),
                        })
                return positions

        except Exception as exc:
            log.error("ccxt.get_positions.failed", error=str(exc))
            return []

    async def get_account_info(self) -> AccountInfo:
        if not self._exchange:
            return AccountInfo(broker=self._exchange_id)

        try:
            balance = await self._exchange.fetch_balance()
            total = balance.get("total", {})
            free = balance.get("free", {})
            usdt_total = float(total.get("USDT", 0) or total.get("USD", 0) or 0)
            usdt_free = float(free.get("USDT", 0) or free.get("USD", 0) or 0)

            return AccountInfo(
                balance=usdt_total,
                equity=usdt_total,
                free_margin=usdt_free,
                currency="USDT",
                broker=self._exchange_id,
            )
        except Exception as exc:
            log.error("ccxt.account_info.failed", error=str(exc))
            return AccountInfo(broker=self._exchange_id)
