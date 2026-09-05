"""Normalize persisted SSI SDK messages into the small live/replay event model."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator
from ssi_sdk.models import QuoteMessage, TradeMessage

MARKET_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")
_TRADE_ADAPTER = TypeAdapter(TradeMessage)
_QUOTE_ADAPTER = TypeAdapter(QuoteMessage)


class MarketEventError(ValueError):
    """A captured SDK message cannot safely enter deterministic market state."""


class StreamEnvelope(BaseModel):
    """Source-independent fields persisted for each SDK callback."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stream_session_id: str = Field(min_length=1)
    receive_sequence: int = Field(ge=1)
    message_type: str = Field(min_length=1)
    symbol: str | None
    source_time_text: str | None
    received_at: datetime
    message_json: str = Field(min_length=2)
    message_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("received_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")
        return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class EventPosition:
    stream_session_id: str
    receive_sequence: int


@dataclass(frozen=True, slots=True)
class Trade:
    position: EventPosition
    symbol: str
    event_time: datetime
    received_at: datetime
    price: Decimal
    quantity: int
    side: Literal["BUY", "SELL"]
    cumulative_volume: int | None


@dataclass(frozen=True, slots=True)
class AuctionObservation:
    position: EventPosition
    symbol: str
    event_time: datetime
    received_at: datetime


@dataclass(frozen=True, slots=True)
class QuoteLevel:
    level: int
    price: Decimal
    quantity: int


@dataclass(frozen=True, slots=True)
class QuoteSnapshot:
    position: EventPosition
    symbol: str
    event_time: datetime
    received_at: datetime
    bids: tuple[QuoteLevel, ...]
    asks: tuple[QuoteLevel, ...]
    is_complete: bool


MarketEvent = Trade | AuctionObservation | QuoteSnapshot


def _event_time(value: str, received_at: datetime, timezone: ZoneInfo) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone)
    except ValueError as error:
        raise MarketEventError("unsupported SSI trading_time") from error
    normalized = parsed.astimezone(UTC)
    if normalized > received_at:
        raise MarketEventError("provider event time follows capture receipt time")
    return normalized


def _price(value: int | float) -> Decimal:
    price = Decimal(str(value))
    if not price.is_finite() or price != price.to_integral_value():
        raise MarketEventError("SSI price must be a finite whole VND amount")
    return price.quantize(Decimal(1))


def _levels(
    prices: tuple[Decimal, ...],
    quantities: tuple[int, ...],
    *,
    depth: int,
    descending: bool,
) -> tuple[QuoteLevel, ...]:
    for price, quantity in zip(prices, quantities, strict=True):
        if price < 0 or quantity < 0 or (price == 0) != (quantity == 0):
            raise MarketEventError("quote price and quantity pairs are invalid")
    if any(value != 0 for value in (*prices[depth:], *quantities[depth:])):
        raise MarketEventError("quote contains populated levels beyond the configured depth")
    levels = tuple(
        QuoteLevel(level=index, price=price, quantity=quantity)
        for index, (price, quantity) in enumerate(
            zip(prices[:depth], quantities[:depth], strict=True), start=1
        )
        if price > 0 and quantity > 0
    )
    ordered = all(
        (previous.price > current.price if descending else previous.price < current.price)
        for previous, current in pairwise(levels)
    )
    if not ordered:
        raise MarketEventError("quote levels are not ordered best-to-worst")
    return levels


def decode_event(
    envelope: StreamEnvelope,
    *,
    quote_depth: int,
    timezone: ZoneInfo = MARKET_TIMEZONE,
) -> MarketEvent | None:
    """Decode only certified business messages; acknowledgements remain raw evidence."""
    if hashlib.sha256(envelope.message_json.encode()).hexdigest() != envelope.message_sha256:
        raise MarketEventError("message_json checksum does not match its capture envelope")
    position = EventPosition(envelope.stream_session_id, envelope.receive_sequence)
    try:
        if envelope.message_type == "TradeMessage":
            payload = _TRADE_ADAPTER.validate_json(envelope.message_json)
            if (
                envelope.symbol != payload.symbol
                or envelope.source_time_text != payload.trading_time
            ):
                raise MarketEventError("TradeMessage capture lineage does not match its payload")
            event_time = _event_time(payload.trading_time, envelope.received_at, timezone)
            if payload.type.value != "trade":
                raise MarketEventError("TradeMessage has an unexpected provider type")
            price = _price(payload.price)
            if price == 0 and payload.quantity == 0 and payload.side.upper() == "U":
                return AuctionObservation(
                    position=position,
                    symbol=payload.symbol.upper(),
                    event_time=event_time,
                    received_at=envelope.received_at,
                )
            raw_side = payload.side.upper()
            side: Literal["BUY", "SELL"] | None
            if raw_side == "B":
                side = "BUY"
            elif raw_side == "S":
                side = "SELL"
            else:
                side = None
            if price <= 0 or payload.quantity <= 0 or side is None:
                raise MarketEventError("TradeMessage is neither a trade nor an auction observation")
            if payload.total_volume < payload.quantity:
                raise MarketEventError("cumulative trade volume is smaller than trade quantity")
            return Trade(
                position=position,
                symbol=payload.symbol.upper(),
                event_time=event_time,
                received_at=envelope.received_at,
                price=price,
                quantity=payload.quantity,
                side=side,
                cumulative_volume=payload.total_volume,
            )
        if envelope.message_type == "QuoteMessage":
            payload = _QUOTE_ADAPTER.validate_json(envelope.message_json)
            if (
                envelope.symbol != payload.symbol
                or envelope.source_time_text != payload.trading_time
            ):
                raise MarketEventError("QuoteMessage capture lineage does not match its payload")
            if payload.type.value != "quote":
                raise MarketEventError("QuoteMessage has an unexpected provider type")
            arrays = (
                payload.bid_prices,
                payload.bid_volumes,
                payload.ask_prices,
                payload.ask_volumes,
            )
            if any(len(values) != 10 for values in arrays):
                raise MarketEventError("SSI quote arrays must contain ten positions")
            bids = _levels(
                tuple(_price(value) for value in payload.bid_prices),
                tuple(payload.bid_volumes),
                depth=quote_depth,
                descending=True,
            )
            asks = _levels(
                tuple(_price(value) for value in payload.ask_prices),
                tuple(payload.ask_volumes),
                depth=quote_depth,
                descending=False,
            )
            return QuoteSnapshot(
                position=position,
                symbol=payload.symbol.upper(),
                event_time=_event_time(payload.trading_time, envelope.received_at, timezone),
                received_at=envelope.received_at,
                bids=bids,
                asks=asks,
                is_complete=(
                    len(bids) == quote_depth
                    and len(asks) == quote_depth
                    and bids[0].price < asks[0].price
                ),
            )
    except ValueError as error:
        if isinstance(error, MarketEventError):
            raise
        raise MarketEventError(f"invalid {envelope.message_type}") from error
    return None
