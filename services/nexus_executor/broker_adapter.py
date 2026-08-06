"""
Nexus Executor — Abstract broker adapter interface.

Every broker implementation (MT5, IBKR, CCXT) inherits from ``BrokerAdapter``
so the execution layer can swap brokers transparently.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum, unique
from typing import Any


@unique
class OrderType(StrEnum):
    MARKET      = "MARKET"
    LIMIT       = "LIMIT"
    STOP        = "STOP"
    STOP_LIMIT  = "STOP_LIMIT"


@unique
class OrderSide(StrEnum):
    BUY  = "BUY"
    SELL = "SELL"


@unique
class OrderStatus(StrEnum):
    PENDING   = "PENDING"
    FILLED    = "FILLED"
    PARTIAL   = "PARTIAL"
    CANCELLED = "CANCELLED"
    REJECTED  = "REJECTED"
    ERROR     = "ERROR"


@dataclass
class OrderRequest:
    symbol: str
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    size: float = 0.01
    price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    slippage: int = 10
    comment: str = "nexus:trade"
    magic: int = 123456
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderResponse:
    order_id: str = ""
    symbol: str = ""
    side: str = ""
    order_type: str = ""
    requested_price: float = 0.0
    fill_price: float = 0.0
    size: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    error: str = ""
    broker: str = ""
    filled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw_response: dict[str, Any] = field(default_factory=dict)

    def is_success(self) -> bool:
        return self.status in (OrderStatus.FILLED, OrderStatus.PARTIAL)


@dataclass
class AccountInfo:
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    free_margin: float = 0.0
    margin_level: float = 0.0
    currency: str = "USD"
    broker: str = ""
    server: str = ""


class BrokerAdapter(ABC):
    """Abstract interface for all broker integrations."""

    @abstractmethod
    async def connect(self) -> bool:
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        ...

    @abstractmethod
    async def send_order(self, order: OrderRequest) -> OrderResponse:
        ...

    @abstractmethod
    async def modify_order(
        self,
        position_id: str,
        *,
        sl: float | None = None,
        tp: float | None = None,
    ) -> OrderResponse:
        ...

    @abstractmethod
    async def close_position(
        self,
        position_id: str,
        *,
        size: float | None = None,
    ) -> OrderResponse:
        ...

    @abstractmethod
    async def get_positions(self) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    async def get_account_info(self) -> AccountInfo:
        ...
