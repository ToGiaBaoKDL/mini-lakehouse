"""Deterministic, side-effect-free market state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from t0_trading.configuration import TradingVersion
from t0_trading.market.events import (
    MarketEvent,
    QuoteSnapshot,
    StreamEnvelope,
    Trade,
    decode_event,
)

SEQUENCE_GAP = "SEQUENCE_GAP"
SEQUENCE_REGRESSION = "SEQUENCE_REGRESSION"
RECEIPT_TIME_REGRESSION = "RECEIPT_TIME_REGRESSION"
TRADE_TIME_REGRESSION = "TRADE_TIME_REGRESSION"
QUOTE_TIME_REGRESSION = "QUOTE_TIME_REGRESSION"
LATE_TRADE = "LATE_TRADE"
VWAP_QUANTUM = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    start: datetime
    end: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: int
    value: Decimal
    trade_count: int
    buy_volume: int
    sell_volume: int
    vwap: Decimal
    available_at: datetime


@dataclass(slots=True)
class _OpenBar:
    symbol: str
    start: datetime
    end: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: int
    value: Decimal
    trade_count: int
    buy_volume: int
    sell_volume: int

    @classmethod
    def from_trade(cls, trade: Trade, interval: timedelta) -> _OpenBar:
        start = _floor_time(trade.event_time, int(interval.total_seconds()))
        value = trade.price * trade.quantity
        return cls(
            symbol=trade.symbol,
            start=start,
            end=start + interval,
            open_price=trade.price,
            high_price=trade.price,
            low_price=trade.price,
            close_price=trade.price,
            volume=trade.quantity,
            value=value,
            trade_count=1,
            buy_volume=trade.quantity if trade.side == "BUY" else 0,
            sell_volume=trade.quantity if trade.side == "SELL" else 0,
        )

    def add(self, trade: Trade) -> None:
        self.high_price = max(self.high_price, trade.price)
        self.low_price = min(self.low_price, trade.price)
        self.close_price = trade.price
        self.volume += trade.quantity
        self.value += trade.price * trade.quantity
        self.trade_count += 1
        if trade.side == "BUY":
            self.buy_volume += trade.quantity
        else:
            self.sell_volume += trade.quantity

    def close(self, available_at: datetime) -> Bar:
        return Bar(
            symbol=self.symbol,
            start=self.start,
            end=self.end,
            open_price=self.open_price,
            high_price=self.high_price,
            low_price=self.low_price,
            close_price=self.close_price,
            volume=self.volume,
            value=self.value,
            trade_count=self.trade_count,
            buy_volume=self.buy_volume,
            sell_volume=self.sell_volume,
            vwap=(self.value / self.volume).quantize(VWAP_QUANTUM, rounding=ROUND_HALF_UP),
            available_at=available_at,
        )


@dataclass(frozen=True, slots=True)
class MarketUpdate:
    event: MarketEvent | None
    finalized_bars: tuple[Bar, ...] = ()
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketHealth:
    symbol: str
    evaluated_at: datetime
    ready: bool
    reasons: tuple[str, ...]


@dataclass(slots=True)
class _SymbolState:
    latest_trade: Trade | None = None
    latest_quote: QuoteSnapshot | None = None
    last_trade_time: datetime | None = None
    last_quote_time: datetime | None = None
    open_bar: _OpenBar | None = None
    closed_through: datetime | None = None


def _floor_time(value: datetime, interval_seconds: int) -> datetime:
    epoch_seconds = int(value.timestamp())
    return datetime.fromtimestamp(
        epoch_seconds - epoch_seconds % interval_seconds,
        tz=UTC,
    )


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


class MarketState:
    """Consume captured envelopes in receipt order and maintain bounded hot state."""

    def __init__(self, configuration: TradingVersion) -> None:
        self.configuration = configuration
        self._symbols = {symbol: _SymbolState() for symbol in configuration.market.symbols}
        self._last_sequence: dict[str, int] = {}
        self._last_received_at: dict[str, datetime] = {}
        self._integrity_issues: set[str] = set()
        self._interval = timedelta(seconds=configuration.market.bar_interval_seconds)
        self._timezone = ZoneInfo(configuration.market.timezone)

    @property
    def integrity_issues(self) -> tuple[str, ...]:
        return tuple(sorted(self._integrity_issues))

    def _sequence_issues(self, envelope: StreamEnvelope) -> tuple[str, ...]:
        issues: list[str] = []
        previous = self._last_sequence.get(envelope.stream_session_id)
        if previous is None:
            if envelope.receive_sequence != 1:
                issues.append(SEQUENCE_GAP)
        elif envelope.receive_sequence <= previous:
            issues.append(SEQUENCE_REGRESSION)
        elif envelope.receive_sequence != previous + 1:
            issues.append(SEQUENCE_GAP)
        previous_received_at = self._last_received_at.get(envelope.stream_session_id)
        if previous_received_at is not None and envelope.received_at < previous_received_at:
            issues.append(RECEIPT_TIME_REGRESSION)
        if SEQUENCE_REGRESSION not in issues:
            self._last_sequence[envelope.stream_session_id] = envelope.receive_sequence
            if previous_received_at is None or envelope.received_at >= previous_received_at:
                self._last_received_at[envelope.stream_session_id] = envelope.received_at
        self._integrity_issues.update(issues)
        return tuple(issues)

    def apply(self, envelope: StreamEnvelope) -> MarketUpdate:
        issues = self._sequence_issues(envelope)
        if SEQUENCE_REGRESSION in issues or RECEIPT_TIME_REGRESSION in issues:
            return MarketUpdate(event=None, issues=issues)
        event = decode_event(
            envelope,
            quote_depth=self.configuration.market.quote_depth,
            timezone=self._timezone,
        )
        if event is None:
            return MarketUpdate(event=None, issues=issues)
        if not self.configuration.contains(event.event_time.astimezone(self._timezone).date()):
            raise ValueError("event falls outside the configuration effective interval")
        symbol_state = self._symbols.get(event.symbol)
        if symbol_state is None:
            raise ValueError(f"event symbol is outside the configured universe: {event.symbol}")
        finalized: tuple[Bar, ...] = ()
        event_issues: list[str] = list(issues)
        if isinstance(event, QuoteSnapshot):
            if (
                symbol_state.last_quote_time is not None
                and event.event_time < symbol_state.last_quote_time
            ):
                event_issues.append(QUOTE_TIME_REGRESSION)
            else:
                symbol_state.latest_quote = event
                symbol_state.last_quote_time = event.event_time
        else:
            if (
                symbol_state.last_trade_time is not None
                and event.event_time < symbol_state.last_trade_time
            ):
                event_issues.append(TRADE_TIME_REGRESSION)
            else:
                if isinstance(event, Trade):
                    bar_end = (
                        _floor_time(event.event_time, int(self._interval.total_seconds()))
                        + self._interval
                    )
                    if (
                        symbol_state.closed_through is not None
                        and bar_end <= symbol_state.closed_through
                    ):
                        event_issues.append(LATE_TRADE)
                    else:
                        finalized = self._apply_trade(symbol_state, event)
                        symbol_state.latest_trade = event
                        symbol_state.last_trade_time = event.event_time
                else:
                    symbol_state.last_trade_time = event.event_time
        self._integrity_issues.update(event_issues)
        applied = not any(
            issue in {LATE_TRADE, QUOTE_TIME_REGRESSION, TRADE_TIME_REGRESSION}
            for issue in event_issues
        )
        return MarketUpdate(
            event=event if applied else None,
            finalized_bars=finalized,
            issues=tuple(event_issues),
        )

    def _apply_trade(self, state: _SymbolState, trade: Trade) -> tuple[Bar, ...]:
        start = _floor_time(trade.event_time, int(self._interval.total_seconds()))
        if state.open_bar is None:
            state.open_bar = _OpenBar.from_trade(trade, self._interval)
            return ()
        if start == state.open_bar.start:
            state.open_bar.add(trade)
            return ()
        if start < state.open_bar.start:
            self._integrity_issues.add(TRADE_TIME_REGRESSION)
            return ()
        finalized = state.open_bar.close(trade.received_at)
        state.closed_through = state.open_bar.end
        state.open_bar = _OpenBar.from_trade(trade, self._interval)
        return (finalized,)

    def advance(self, event_time: datetime, *, available_at: datetime) -> tuple[Bar, ...]:
        """Finalize bars closed by an explicit live or virtual-clock boundary."""
        event_time = _aware_utc(event_time, "event_time")
        available_at = _aware_utc(available_at, "available_at")
        if available_at < event_time:
            raise ValueError("available_at must not precede event_time")
        finalized: list[Bar] = []
        boundary = _floor_time(event_time, int(self._interval.total_seconds()))
        for state in self._symbols.values():
            if state.open_bar is not None and state.open_bar.end <= event_time:
                finalized.append(state.open_bar.close(available_at))
                state.open_bar = None
            if state.closed_through is None or boundary > state.closed_through:
                state.closed_through = boundary
        return tuple(sorted(finalized, key=lambda bar: (bar.end, bar.symbol)))

    def health(self, symbol: str, *, evaluated_at: datetime) -> MarketHealth:
        evaluated_at = _aware_utc(evaluated_at, "evaluated_at")
        state = self._symbols.get(symbol)
        if state is None:
            raise ValueError(f"symbol is outside the configured universe: {symbol}")
        reasons = list(self.integrity_issues)
        if state.latest_trade is None:
            reasons.append("MISSING_TRADE")
        elif state.latest_trade.received_at > evaluated_at:
            reasons.append("FUTURE_TRADE")
        elif evaluated_at - state.latest_trade.received_at > timedelta(
            seconds=self.configuration.data_quality.trade_stale_after_seconds
        ):
            reasons.append("STALE_TRADE")
        if state.latest_quote is None:
            reasons.append("MISSING_QUOTE")
        else:
            if state.latest_quote.received_at > evaluated_at:
                reasons.append("FUTURE_QUOTE")
            elif evaluated_at - state.latest_quote.received_at > timedelta(
                seconds=self.configuration.data_quality.quote_stale_after_seconds
            ):
                reasons.append("STALE_QUOTE")
            if not state.latest_quote.is_complete:
                reasons.append("INCOMPLETE_QUOTE")
        ordered = tuple(dict.fromkeys(reasons))
        return MarketHealth(
            symbol=symbol,
            evaluated_at=evaluated_at,
            ready=not ordered,
            reasons=ordered,
        )

    def latest_quote(self, symbol: str) -> QuoteSnapshot | None:
        state = self._symbols.get(symbol)
        if state is None:
            raise ValueError(f"symbol is outside the configured universe: {symbol}")
        return state.latest_quote
