"""One SDK-backed normalization contract for SSI minute-bar revisions."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter
from ssi_sdk.models import IntervalMessage

from t0_trading.identity import sha256
from t0_trading.market.events import StreamEnvelope, provider_price, provider_timestamp

_ADAPTER = TypeAdapter(IntervalMessage)


@dataclass(frozen=True, slots=True)
class ProviderBar:
    symbol: str
    start: datetime
    observed_at: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: int

    @property
    def values(self) -> tuple[Decimal, Decimal, Decimal, Decimal, int]:
        return (self.open_price, self.high_price, self.low_price, self.close_price, self.volume)


def provider_bar(envelope: StreamEnvelope, timezone: ZoneInfo) -> ProviderBar | None:
    """Normalize a revision, not a final bar; its receipt time stays in the envelope."""
    if envelope.message_type != "IntervalMessage":
        return None
    if sha256(envelope.message_json.encode()) != envelope.message_sha256:
        raise ValueError("SSI IntervalMessage checksum is inconsistent")
    try:
        message = _ADAPTER.validate_json(envelope.message_json)
    except ValueError as error:
        raise ValueError("invalid SSI IntervalMessage") from error
    if (
        envelope.subscription_context != "symbols"
        or envelope.symbol != message.symbol
        or envelope.source_time_text != message.trading_time
        or message.type.value != "trade"
        or message.volume <= 0
    ):
        raise ValueError("SSI IntervalMessage lineage is inconsistent")
    start = provider_timestamp(message.interval_time, timezone).astimezone(timezone)
    observed_at = provider_timestamp(message.trading_time, timezone).astimezone(timezone)
    if (
        start.replace(second=0, microsecond=0) != start
        or not start <= observed_at < start + timedelta(minutes=1)
        or observed_at > envelope.received_at
    ):
        raise ValueError("SSI IntervalMessage timestamps are inconsistent")
    open_price, high_price, low_price, close_price = (
        provider_price(value) for value in (message.open, message.high, message.low, message.close)
    )
    if (
        low_price <= 0
        or low_price > min(open_price, close_price)
        or high_price < max(open_price, close_price)
    ):
        raise ValueError("SSI IntervalMessage OHLC values are inconsistent")
    return ProviderBar(
        symbol=message.symbol.upper(),
        start=start,
        observed_at=observed_at,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        volume=message.volume,
    )
