"""
Nexus Executor — MetaTrader 5 broker implementation.

Uses the MetaTrader5 Python package for market/limit orders, SL/TP
modification, partial close, and slippage control with automatic retry.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import time as _time
from datetime import datetime, timezone
from typing import Any, Callable

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

_MAX_RETRIES = 3
_RETRY_DELAY = 0.5
_MT5_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4,  # parallel order execution for scalping speed
    thread_name_prefix="mt5-executor-com",
)


async def _mt5_call(fn: Callable[[], Any]) -> Any:
    """Run all MetaTrader5 calls on one dedicated COM thread."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_MT5_EXECUTOR, fn)


def _is_autotrading_disabled(error: str | None) -> bool:
    """True when MT5 rejected the order because AutoTrading is off (retcode 10027).

    The broker comment is "AutoTrading disabled by client". This is a manual
    safety toggle in the terminal that the API cannot enable by design, so it
    is never worth retrying.
    """
    if not error:
        return False
    return "autotrading disabled" in error.lower()


def _symbol_aliases() -> dict[str, str]:
    raw = os.getenv("MT5_SYMBOL_ALIASES", "").strip()
    aliases: dict[str, str] = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        logical = key.strip().upper().replace("/", "")
        broker_symbol = value.strip()
        if logical and broker_symbol:
            aliases[logical] = broker_symbol
    return aliases


def _resolve_symbol(symbol: str) -> str:
    logical = symbol.upper().replace("/", "")
    return _symbol_aliases().get(logical, logical)


class MT5Executor(BrokerAdapter):
    """MetaTrader 5 execution adapter."""

    def __init__(
        self,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        path: str | None = None,
    ) -> None:
        self._login = login or int(os.getenv("MT5_LOGIN", "0"))
        self._password = password or os.getenv("MT5_PASSWORD", "")
        self._server = server or os.getenv("MT5_SERVER", "")
        self._path = path or os.getenv("MT5_PATH", "")
        self._connected = False
        self._mt5: Any = None
        self._server_utc_offset_sec: float | None = None

    def _server_offset(self, mt5: Any, symbol: str) -> float:
        """Seconds the broker server clock runs ahead of UTC.

        MT5 reports position/tick epochs in *server* time; storing them as UTC
        skewed ``opened_at`` by the offset (XMGlobal: +3h → trades appeared
        'closed before opened' and durations were wrong). Estimated from the
        freshest tick vs the local UTC clock, rounded to the nearest 30 min.
        """
        if self._server_utc_offset_sec is not None:
            return self._server_utc_offset_sec
        try:
            tick = mt5.symbol_info_tick(symbol)
            if tick is not None and tick.time:
                raw = float(tick.time) - _time.time()
                offset = round(raw / 1800.0) * 1800.0
                # A stale tick (market closed) makes the estimate unreliable —
                # only cache plausible offsets and never a large negative one.
                if -3600.0 <= offset <= 14 * 3600:
                    self._server_utc_offset_sec = offset
                    return offset
        except Exception:
            pass
        return 0.0

    def _import_mt5(self) -> Any:
        if self._mt5 is None:
            try:
                import MetaTrader5 as mt5
                self._mt5 = mt5
            except ImportError:
                log.error("mt5.import_failed", msg="MetaTrader5 package not installed")
                raise RuntimeError("MetaTrader5 package not available")
        return self._mt5

    async def connect(self) -> bool:
        mt5 = self._import_mt5()

        def _init():
            kwargs: dict[str, Any] = {}
            if self._path:
                kwargs["path"] = self._path
            if not mt5.initialize(**kwargs):
                return False
            if self._login and self._password and self._server:
                return mt5.login(self._login, self._password, self._server)
            return True

        result = await _mt5_call(_init)
        self._connected = result
        log.info("mt5.connect", success=result, server=self._server)
        return result

    async def disconnect(self) -> None:
        if self._connected:
            mt5 = self._import_mt5()
            await _mt5_call(mt5.shutdown)
            self._connected = False
            log.info("mt5.disconnected")

    async def send_order(self, order: OrderRequest) -> OrderResponse:
        mt5 = self._import_mt5()
        if not self._connected:
            connected = await self.connect()
            if not connected:
                return OrderResponse(
                    symbol=order.symbol,
                    side=order.side.value,
                    order_type=order.order_type.value,
                    size=order.size,
                    status=OrderStatus.ERROR,
                    error="MT5 is not connected; check terminal login/server/path",
                    broker="MT5",
                )

        def _send() -> OrderResponse:
            broker_symbol = _resolve_symbol(order.symbol)
            symbol_info = mt5.symbol_info(broker_symbol)
            if symbol_info is None:
                return OrderResponse(
                    symbol=order.symbol,
                    status=OrderStatus.ERROR,
                    error=f"Symbol {broker_symbol} not found (logical={order.symbol})",
                    broker="MT5",
                )
            if not symbol_info.visible:
                mt5.symbol_select(broker_symbol, True)

            tick = mt5.symbol_info_tick(broker_symbol)
            if tick is None:
                return OrderResponse(
                    symbol=order.symbol,
                    status=OrderStatus.ERROR,
                    error="No tick data available",
                    broker="MT5",
                )

            if order.order_type == OrderType.MARKET:
                action = mt5.TRADE_ACTION_DEAL
                price = tick.ask if order.side == OrderSide.BUY else tick.bid
                type_mt5 = mt5.ORDER_TYPE_BUY if order.side == OrderSide.BUY else mt5.ORDER_TYPE_SELL
            elif order.order_type == OrderType.LIMIT:
                action = mt5.TRADE_ACTION_PENDING
                price = order.price
                if order.side == OrderSide.BUY:
                    type_mt5 = mt5.ORDER_TYPE_BUY_LIMIT
                else:
                    type_mt5 = mt5.ORDER_TYPE_SELL_LIMIT
            elif order.order_type == OrderType.STOP:
                action = mt5.TRADE_ACTION_PENDING
                price = order.price
                if order.side == OrderSide.BUY:
                    type_mt5 = mt5.ORDER_TYPE_BUY_STOP
                else:
                    type_mt5 = mt5.ORDER_TYPE_SELL_STOP
            else:
                return OrderResponse(
                    symbol=order.symbol,
                    status=OrderStatus.ERROR,
                    error=f"Unsupported order type: {order.order_type}",
                    broker="MT5",
                )

            request = {
                "action": action,
                "symbol": broker_symbol,
                "volume": order.size,
                "type": type_mt5,
                "price": price,
                "sl": order.sl,
                "tp": order.tp,
                "deviation": order.slippage,
                "magic": order.magic,
                "comment": order.comment,
                "type_time": mt5.ORDER_TIME_GTC,
            }

            filling_modes = [
                mt5.ORDER_FILLING_IOC,
                mt5.ORDER_FILLING_FOK,
                getattr(mt5, "ORDER_FILLING_RETURN", mt5.ORDER_FILLING_IOC),
            ]
            result = None
            for filling_mode in dict.fromkeys(filling_modes):
                request["type_filling"] = filling_mode
                result = mt5.order_send(request)
                invalid_fill = getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030)
                if result is None or result.retcode != invalid_fill:
                    break

            if result is None:
                return OrderResponse(
                    symbol=order.symbol,
                    status=OrderStatus.ERROR,
                    error=f"MT5 error: {mt5.last_error()}",
                    broker="MT5",
                )

            if result.retcode == mt5.TRADE_RETCODE_DONE:
                status = OrderStatus.FILLED
            elif result.retcode == mt5.TRADE_RETCODE_PLACED:
                status = OrderStatus.PENDING
            else:
                status = OrderStatus.REJECTED

            return OrderResponse(
                order_id=str(result.order),
                symbol=order.symbol,
                side=order.side.value,
                order_type=order.order_type.value,
                requested_price=price,
                fill_price=result.price,
                size=result.volume,
                sl=order.sl,
                tp=order.tp,
                status=status,
                error=result.comment if status == OrderStatus.REJECTED else "",
                broker="MT5",
                raw_response={
                    "retcode": result.retcode,
                    "deal": result.deal,
                    "order": result.order,
                    "comment": result.comment,
                },
            )

        for attempt in range(_MAX_RETRIES):
            resp = await _mt5_call(_send)
            if resp.is_success() or resp.status == OrderStatus.PENDING:
                log.info(
                    "mt5.order.sent",
                    order_id=resp.order_id,
                    status=resp.status,
                    symbol=order.symbol,
                )
                return resp

            # "AutoTrading disabled by client" (retcode 10027) is a terminal-side
            # safety toggle — retrying can never fix it. Surface a loud, actionable
            # message and stop instead of silently burning retries.
            if _is_autotrading_disabled(resp.error):
                log.error(
                    "mt5.autotrading_disabled",
                    symbol=order.symbol,
                    action="ENABLE the 'Algo Trading' button in the MT5 terminal "
                           "(toolbar) and Tools > Options > Expert Advisors > "
                           "'Allow Algorithmic Trading'. Order NOT sent.",
                )
                resp.error = (
                    "AutoTrading disabled in MT5 terminal — enable the 'Algo Trading' "
                    "button to allow automated orders."
                )
                return resp

            if attempt < _MAX_RETRIES - 1:
                log.warning(
                    "mt5.order.retry",
                    attempt=attempt + 1,
                    error=resp.error,
                )
                await asyncio.sleep(_RETRY_DELAY * (attempt + 1))

        log.error("mt5.order.failed", error=resp.error, symbol=order.symbol)
        return resp

    async def modify_order(
        self,
        position_id: str,
        *,
        sl: float | None = None,
        tp: float | None = None,
    ) -> OrderResponse:
        mt5 = self._import_mt5()

        def _modify() -> OrderResponse:
            ticket = int(position_id)
            position = mt5.positions_get(ticket=ticket)
            if not position:
                return OrderResponse(
                    order_id=position_id,
                    status=OrderStatus.ERROR,
                    error=f"Position {position_id} not found",
                    broker="MT5",
                )

            pos = position[0]
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "symbol": pos.symbol,
                "sl": sl if sl is not None else pos.sl,
                "tp": tp if tp is not None else pos.tp,
            }

            result = mt5.order_send(request)
            if result is None:
                return OrderResponse(
                    order_id=position_id,
                    status=OrderStatus.ERROR,
                    error=f"MT5 error: {mt5.last_error()}",
                    broker="MT5",
                )

            success = result.retcode == mt5.TRADE_RETCODE_DONE
            return OrderResponse(
                order_id=position_id,
                symbol=pos.symbol,
                sl=sl or pos.sl,
                tp=tp or pos.tp,
                status=OrderStatus.FILLED if success else OrderStatus.REJECTED,
                error="" if success else result.comment,
                broker="MT5",
            )

        return await _mt5_call(_modify)

    async def close_position(
        self,
        position_id: str,
        *,
        size: float | None = None,
    ) -> OrderResponse:
        mt5 = self._import_mt5()

        def _close() -> OrderResponse:
            ticket = int(position_id)
            position = mt5.positions_get(ticket=ticket)
            if not position:
                return OrderResponse(
                    order_id=position_id,
                    status=OrderStatus.ERROR,
                    error=f"Position {position_id} not found",
                    broker="MT5",
                )

            pos = position[0]
            close_volume = size if size else pos.volume
            close_type = (
                mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            )
            tick = mt5.symbol_info_tick(pos.symbol)
            price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": ticket,
                "symbol": pos.symbol,
                "volume": close_volume,
                "type": close_type,
                "price": price,
                "deviation": 20,
                "magic": pos.magic,
                "comment": "nexus_close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            result = mt5.order_send(request)
            if result is None:
                return OrderResponse(
                    order_id=position_id,
                    status=OrderStatus.ERROR,
                    error=f"MT5 error: {mt5.last_error()}",
                    broker="MT5",
                )

            success = result.retcode == mt5.TRADE_RETCODE_DONE
            position_side = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"
            opened_at = datetime.fromtimestamp(
                int(pos.time) - self._server_offset(mt5, pos.symbol),
                tz=timezone.utc,
            )
            return OrderResponse(
                order_id=str(result.order),
                symbol=pos.symbol,
                side=position_side,
                requested_price=pos.price_open,
                fill_price=result.price,
                size=close_volume,
                status=OrderStatus.FILLED if success else OrderStatus.REJECTED,
                error="" if success else result.comment,
                broker="MT5",
                raw_response={
                    "entry_price": pos.price_open,
                    "pnl_usd": pos.profit,
                    "swap": pos.swap,
                    "position_side": position_side,
                    "opened_at": opened_at.isoformat(),
                    "ticket": ticket,
                },
            )

        return await _mt5_call(_close)

    async def get_positions(self) -> list[dict[str, Any]]:
        mt5 = self._import_mt5()

        def _get():
            positions = mt5.positions_get()
            if positions is None:
                return []
            return [
                {
                    "id": str(p.ticket),
                    "symbol": p.symbol,
                    "side": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                    "entry_price": p.price_open,
                    "current_price": p.price_current,
                    "sl": p.sl,
                    "tp": p.tp,
                    "size": p.volume,
                    "pnl_usd": p.profit,
                    "swap": p.swap,
                    "magic": p.magic,
                    "comment": p.comment,
                    "opened_at": datetime.fromtimestamp(
                        int(p.time) - self._server_offset(mt5, p.symbol),
                        tz=timezone.utc,
                    ).isoformat(),
                }
                for p in positions
            ]

        return await _mt5_call(_get)

    async def get_account_info(self) -> AccountInfo:
        mt5 = self._import_mt5()

        def _get():
            info = mt5.account_info()
            if info is None:
                return AccountInfo(broker="MT5")
            return AccountInfo(
                balance=info.balance,
                equity=info.equity,
                margin=info.margin,
                free_margin=info.margin_free,
                margin_level=info.margin_level,
                currency=info.currency,
                broker="MT5",
                server=info.server,
            )

        return await _mt5_call(_get)
