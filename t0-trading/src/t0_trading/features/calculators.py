"""Pure calculations shared by live and replay feature evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, localcontext
from itertools import pairwise
from typing import TypedDict

from t0_trading.features.model import WindowFeatures
from t0_trading.market.events import QuoteSnapshot, Trade
from t0_trading.numeric import BPS_QUANTUM, PRICE_QUANTUM, basis_points, ratio


class BookValues(TypedDict):
    mid_price: Decimal | None
    microprice: Decimal | None
    microprice_deviation_bps: Decimal | None
    spread: Decimal | None
    spread_bps: Decimal | None
    bid_depth: int | None
    ask_depth: int | None
    level_one_imbalance: Decimal | None
    depth_imbalance: Decimal | None


def book_values(quote: QuoteSnapshot) -> BookValues:
    """Calculate scale-aware Top-N book state without inferring hidden orders."""
    if not quote.is_complete or not quote.bids or not quote.asks:
        raise ValueError("book features require one complete non-crossed quote")
    best_bid = quote.bids[0]
    best_ask = quote.asks[0]
    spread = best_ask.price - best_bid.price
    if spread <= 0:
        raise ValueError("book features require a positive spread")
    midpoint = ((best_bid.price + best_ask.price) / 2).quantize(
        PRICE_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    level_one_depth = best_bid.quantity + best_ask.quantity
    microprice = (
        (best_ask.price * best_bid.quantity + best_bid.price * best_ask.quantity) / level_one_depth
    ).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)
    bid_depth = sum(level.quantity for level in quote.bids)
    ask_depth = sum(level.quantity for level in quote.asks)
    return {
        "mid_price": midpoint,
        "microprice": microprice,
        "microprice_deviation_bps": basis_points(microprice - midpoint, midpoint),
        "spread": spread,
        "spread_bps": basis_points(spread, midpoint),
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "level_one_imbalance": ratio(
            best_bid.quantity - best_ask.quantity,
            level_one_depth,
        ),
        "depth_imbalance": ratio(bid_depth - ask_depth, bid_depth + ask_depth),
    }


def level_one_book_flow(previous: QuoteSnapshot, current: QuoteSnapshot) -> int:
    """Return Cont-style best-level order-flow imbalance for one quote transition."""
    if not previous.is_complete or not current.is_complete:
        raise ValueError("book flow requires complete quotes")
    previous_bid, previous_ask = previous.bids[0], previous.asks[0]
    current_bid, current_ask = current.bids[0], current.asks[0]
    bid_flow = (current_bid.quantity if current_bid.price >= previous_bid.price else 0) - (
        previous_bid.quantity if current_bid.price <= previous_bid.price else 0
    )
    ask_flow = (current_ask.quantity if current_ask.price <= previous_ask.price else 0) - (
        previous_ask.quantity if current_ask.price >= previous_ask.price else 0
    )
    return bid_flow - ask_flow


def window_values(
    *,
    window_seconds: int,
    trades: Sequence[Trade],
    book_flows: Sequence[int],
) -> WindowFeatures:
    """Calculate one trailing window from already point-in-time-filtered observations."""
    trade_count = len(trades)
    if trade_count == 0:
        return WindowFeatures(
            window_seconds=window_seconds,
            trade_count=0,
            quote_change_count=len(book_flows),
            trade_volume=0,
            signed_trade_volume=0,
            trade_volume_per_second=None,
            trade_volume_imbalance=None,
            level_one_order_flow_imbalance=sum(book_flows),
            price_return_bps=None,
            realized_volatility_bps=None,
            vwap=None,
            last_price_to_vwap_bps=None,
        )

    volume = sum(trade.quantity for trade in trades)
    signed_volume = sum(
        trade.quantity if trade.side == "BUY" else -trade.quantity for trade in trades
    )
    traded_value = sum((trade.price * trade.quantity for trade in trades), Decimal(0))
    vwap = (traded_value / volume).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)
    price_return_bps: Decimal | None = None
    realized_volatility_bps: Decimal | None = None
    if trade_count >= 2:
        price_return_bps = basis_points(
            trades[-1].price - trades[0].price,
            trades[0].price,
        )
        with localcontext() as context:
            context.prec = 34
            squared_returns = sum(
                (
                    ((current.price - previous.price) / previous.price) ** 2
                    for previous, current in pairwise(trades)
                ),
                start=Decimal(0),
            )
            realized_volatility_bps = (squared_returns.sqrt() * Decimal(10_000)).quantize(
                BPS_QUANTUM, rounding=ROUND_HALF_UP
            )
    return WindowFeatures(
        window_seconds=window_seconds,
        trade_count=trade_count,
        quote_change_count=len(book_flows),
        trade_volume=volume,
        signed_trade_volume=signed_volume,
        trade_volume_per_second=ratio(volume, window_seconds),
        trade_volume_imbalance=ratio(signed_volume, volume),
        level_one_order_flow_imbalance=sum(book_flows),
        price_return_bps=price_return_bps,
        realized_volatility_bps=realized_volatility_bps,
        vwap=vwap,
        last_price_to_vwap_bps=basis_points(trades[-1].price - vwap, vwap),
    )
