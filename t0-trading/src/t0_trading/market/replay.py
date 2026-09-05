"""Replay captured messages through the production market-state transition core."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from t0_trading.configuration import TradingVersion
from t0_trading.market.events import StreamEnvelope
from t0_trading.market.state import Bar, MarketState


@dataclass(frozen=True, slots=True)
class ReplayResult:
    state: MarketState
    input_count: int
    business_event_count: int
    issues: tuple[str, ...]
    finalized_bars: tuple[Bar, ...]


def replay(
    envelopes: Iterable[StreamEnvelope],
    configuration: TradingVersion,
    *,
    finish_at: datetime | None = None,
) -> ReplayResult:
    """Preserve captured receipt order; callers must not sort away source evidence."""
    state = MarketState(configuration)
    bars: list[Bar] = []
    input_count = 0
    business_event_count = 0
    latest_received_at: datetime | None = None
    for envelope in envelopes:
        input_count += 1
        latest_received_at = envelope.received_at
        update = state.apply(envelope)
        business_event_count += update.event is not None
        bars.extend(update.finalized_bars)
    if finish_at is not None:
        if latest_received_at is not None and finish_at < latest_received_at:
            raise ValueError("finish_at must not precede the last captured receipt")
        bars.extend(state.advance(finish_at, available_at=finish_at))
    return ReplayResult(
        state=state,
        input_count=input_count,
        business_event_count=business_event_count,
        issues=state.integrity_issues,
        finalized_bars=tuple(bars),
    )
