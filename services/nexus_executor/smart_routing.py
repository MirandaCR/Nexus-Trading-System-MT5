"""
Nexus Executor — Smart order routing.

Decides between MARKET and LIMIT orders based on spread conditions,
calculates optimal limit prices, and validates liquidity before routing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from .broker_adapter import OrderRequest, OrderSide, OrderType

log = structlog.get_logger(__name__)

_SPREAD_LIMIT_THRESHOLD = 1.5
_LIMIT_OFFSET_PCT = 0.3


@dataclass
class RoutingDecision:
    order_type: OrderType
    price: float
    reason: str
    liquidity_ok: bool = True
    adjusted: bool = False


class SmartRouter:
    """Decides optimal order execution path based on market conditions."""

    def __init__(
        self,
        spread_threshold: float = _SPREAD_LIMIT_THRESHOLD,
        limit_offset_pct: float = _LIMIT_OFFSET_PCT,
    ) -> None:
        self._spread_threshold = spread_threshold
        self._limit_offset_pct = limit_offset_pct
        self._avg_spreads: dict[str, float] = {}
        self._tick_counts: dict[str, int] = {}

    def update_spread(self, symbol: str, spread: float) -> None:
        sym = symbol.upper().replace("/", "")
        count = self._tick_counts.get(sym, 0)
        current_avg = self._avg_spreads.get(sym, spread)
        new_avg = (current_avg * count + spread) / (count + 1)
        self._avg_spreads[sym] = new_avg
        self._tick_counts[sym] = count + 1

    # ── Order Type Decision ───────────────────────────────────────────────

    def decide_order_type(self, spread: float, avg_spread: float) -> OrderType:
        if avg_spread <= 0:
            return OrderType.MARKET

        ratio = spread / avg_spread
        if ratio <= self._spread_threshold:
            return OrderType.MARKET
        return OrderType.LIMIT

    # ── Limit Price Calculation ───────────────────────────────────────────

    def calculate_limit_price(
        self,
        side: str | OrderSide,
        bid: float,
        ask: float,
        spread: float,
    ) -> float:
        side_str = side.value if isinstance(side, OrderSide) else side.upper()
        offset = spread * (self._limit_offset_pct / 100.0)

        if side_str == "BUY":
            price = bid + offset
        else:
            price = ask - offset

        return round(price, 5)

    # ── Liquidity Check ───────────────────────────────────────────────────

    def check_liquidity(self, symbol: str, spread: float | None = None) -> bool:
        sym = symbol.upper().replace("/", "")
        avg = self._avg_spreads.get(sym)

        if avg is None:
            return True

        if spread is not None and avg > 0:
            ratio = spread / avg
            if ratio > 5.0:
                log.warning(
                    "smart_router.low_liquidity",
                    symbol=sym,
                    spread_ratio=round(ratio, 2),
                )
                return False

        return True

    # ── Full Routing ──────────────────────────────────────────────────────

    def route_order(
        self,
        order: OrderRequest,
        *,
        bid: float = 0.0,
        ask: float = 0.0,
        current_spread: float = 0.0,
    ) -> RoutingDecision:
        sym = order.symbol.upper().replace("/", "")
        avg_spread = self._avg_spreads.get(sym, current_spread)

        liquidity_ok = self.check_liquidity(sym, current_spread)

        if not liquidity_ok:
            log.warning("smart_router.low_liquidity_reject", symbol=sym)
            return RoutingDecision(
                order_type=OrderType.LIMIT,
                price=self.calculate_limit_price(order.side, bid, ask, current_spread),
                reason="Low liquidity — using limit order for protection",
                liquidity_ok=False,
                adjusted=True,
            )

        order_type = self.decide_order_type(current_spread, avg_spread)

        if order_type == OrderType.MARKET:
            price = ask if order.side == OrderSide.BUY else bid
            return RoutingDecision(
                order_type=OrderType.MARKET,
                price=price,
                reason=f"Spread {current_spread:.5f} within {self._spread_threshold}x avg",
                liquidity_ok=True,
            )
        else:
            price = self.calculate_limit_price(order.side, bid, ask, current_spread)
            return RoutingDecision(
                order_type=OrderType.LIMIT,
                price=price,
                reason=f"Spread {current_spread:.5f} exceeds {self._spread_threshold}x avg — using limit",
                liquidity_ok=True,
                adjusted=True,
            )
