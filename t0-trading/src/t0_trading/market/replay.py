"""Replay captured messages through the production market-state transition core."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from t0_trading.configuration import TradingVersion
from t0_trading.market.events import MarketEvent, StreamEnvelope
from t0_trading.market.session import session_at
from t0_trading.market.state import Bar, MarketState


@dataclass(frozen=True, slots=True)
class ReplayResult:
    state: MarketState
    input_count: int
    business_event_count: int
    event_counts: tuple[tuple[str, int], ...]
    session_counts: tuple[tuple[str, int], ...]
    issues: tuple[str, ...]
    finalized_bars: tuple[Bar, ...]


def replay(
    envelopes: Iterable[StreamEnvelope],
    configuration: TradingVersion,
    *,
    finish_at: datetime | None = None,
    trade_date: date | None = None,
    observe: Callable[[MarketEvent], None] | None = None,
) -> ReplayResult:
    """Preserve captured receipt order; callers must not sort away source evidence."""
    state = MarketState(configuration)
    bars: list[Bar] = []
    input_count = 0
    business_event_count = 0
    event_counts: Counter[str] = Counter()
    session_counts: Counter[str] = Counter()
    latest_received_at: datetime | None = None
    timezone = ZoneInfo(configuration.market.timezone)
    for envelope in envelopes:
        input_count += 1
        latest_received_at = envelope.received_at
        update = state.apply(envelope)
        business_event_count += update.event is not None
        if update.event is not None:
            event_date = update.event.event_time.astimezone(timezone).date()
            if trade_date is not None and event_date != trade_date:
                raise ValueError("market event does not belong to the captured trade date")
            if observe is not None:
                observe(update.event)
            event_counts[type(update.event).__name__] += 1
            session_counts[
                session_at(
                    update.event.event_time,
                    trade_date=event_date,
                    timezone=timezone,
                    schedule=configuration.market.sessions,
                ).value
            ] += 1
        bars.extend(update.finalized_bars)
    if finish_at is not None:
        if latest_received_at is not None and finish_at < latest_received_at:
            raise ValueError("finish_at must not precede the last captured receipt")
        bars.extend(state.advance(finish_at, available_at=finish_at))
    return ReplayResult(
        state=state,
        input_count=input_count,
        business_event_count=business_event_count,
        event_counts=tuple(sorted(event_counts.items())),
        session_counts=tuple(sorted(session_counts.items())),
        issues=state.integrity_issues,
        finalized_bars=tuple(bars),
    )
