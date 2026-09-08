"""Incremental point-in-time feature state shared by live and replay paths."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from t0_trading.configuration import TradingVersion
from t0_trading.features.calculators import (
    BookValues,
    book_values,
    level_one_book_flow,
    window_values,
)
from t0_trading.features.model import FeatureSnapshot, WindowFeatures
from t0_trading.market.events import MarketEvent, QuoteSnapshot, StreamEnvelope, Trade
from t0_trading.market.session import MarketSession, session_at, session_window
from t0_trading.market.state import MarketState, MarketUpdate

_EMPTY_BOOK: BookValues = {
    "mid_price": None,
    "microprice": None,
    "microprice_deviation_bps": None,
    "spread": None,
    "spread_bps": None,
    "bid_depth": None,
    "ask_depth": None,
    "level_one_imbalance": None,
    "depth_imbalance": None,
}


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _age(later: datetime, earlier: datetime | None) -> Decimal | None:
    if earlier is None:
        return None
    delta = later - earlier
    microseconds = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    return Decimal(microseconds) / Decimal(1_000_000)


@dataclass(slots=True)
class _SymbolFeatures:
    session: MarketSession | None = None
    observed_from: datetime | None = None
    latest_trade: Trade | None = None
    latest_quote: QuoteSnapshot | None = None
    trades: deque[Trade] = field(default_factory=deque)
    book_flows: deque[tuple[datetime, int]] = field(default_factory=deque)

    def reset(self, session: MarketSession) -> None:
        self.session = session
        self.observed_from = None
        self.latest_trade = None
        self.latest_quote = None
        self.trades.clear()
        self.book_flows.clear()


class FeatureEngine:
    """Apply each envelope once and emit auditable fixed-cadence snapshots."""

    def __init__(self, configuration: TradingVersion) -> None:
        self.configuration = configuration
        self.market = MarketState(configuration)
        self._timezone = ZoneInfo(configuration.market.timezone)
        self._symbols = {symbol: _SymbolFeatures() for symbol in configuration.market.symbols}
        self._last_received_at: datetime | None = None
        self._last_stream_session_id: str | None = None
        self._last_receive_sequence: int | None = None
        self._last_decision_at: datetime | None = None
        self._max_window = timedelta(seconds=configuration.features.windows_seconds[-1])

    def _session(self, event: MarketEvent) -> MarketSession:
        trade_date = event.event_time.astimezone(self._timezone).date()
        return session_at(
            event.event_time,
            trade_date=trade_date,
            timezone=self._timezone,
            schedule=self.configuration.market.sessions,
        )

    def _prune(self, state: _SymbolFeatures, observed_at: datetime) -> None:
        cutoff = observed_at - self._max_window
        while state.trades and state.trades[0].received_at <= cutoff:
            state.trades.popleft()
        while state.book_flows and state.book_flows[0][0] <= cutoff:
            state.book_flows.popleft()

    def _observe(self, event: MarketEvent) -> None:
        state = self._symbols[event.symbol]
        session = self._session(event)
        if state.session != session:
            state.reset(session)
        if not isinstance(event, (Trade, QuoteSnapshot)):
            return
        if state.observed_from is None:
            state.observed_from = event.received_at
        if isinstance(event, Trade):
            state.latest_trade = event
            state.trades.append(event)
        else:
            previous = state.latest_quote
            if previous is not None and previous.is_complete and event.is_complete:
                state.book_flows.append((event.received_at, level_one_book_flow(previous, event)))
            state.latest_quote = event
        self._prune(state, event.received_at)

    def apply(self, envelope: StreamEnvelope) -> MarketUpdate:
        """Apply one captured callback without emitting an implicit decision."""
        if self._last_decision_at is not None and envelope.received_at < self._last_decision_at:
            raise ValueError("envelope was received before the last emitted decision")
        update = self.market.apply(envelope)
        if update.event is not None:
            self._observe(update.event)
        if self._last_received_at is None or envelope.received_at >= self._last_received_at:
            self._last_received_at = envelope.received_at
            self._last_stream_session_id = envelope.stream_session_id
            self._last_receive_sequence = envelope.receive_sequence
        return update

    def _window(
        self,
        state: _SymbolFeatures,
        *,
        decision_at: datetime,
        seconds: int,
    ) -> WindowFeatures:
        cutoff = decision_at - timedelta(seconds=seconds)
        trades = tuple(trade for trade in state.trades if cutoff < trade.received_at <= decision_at)
        flows = tuple(
            flow for received_at, flow in state.book_flows if cutoff < received_at <= decision_at
        )
        return window_values(
            window_seconds=seconds,
            trades=trades,
            book_flows=flows,
        )

    def _snapshot(
        self,
        symbol: str,
        *,
        decision_at: datetime,
        session: MarketSession,
    ) -> FeatureSnapshot:
        state = self._symbols[symbol]
        if state.session != session:
            state.reset(session)
        self._prune(state, decision_at)
        windows = tuple(
            self._window(state, decision_at=decision_at, seconds=seconds)
            for seconds in self.configuration.features.windows_seconds
        )
        reasons: list[str] = []
        if session.value not in self.configuration.features.decision_sessions:
            reasons.append("SESSION_NOT_ENABLED")
        else:
            session_start, _ = session_window(
                decision_at.astimezone(self._timezone).date(),
                session,
                timezone=self._timezone,
                schedule=self.configuration.market.sessions,
            )
            reasons.extend(
                self.market.health(
                    symbol,
                    evaluated_at=decision_at,
                    required_since=session_start,
                ).reasons
            )
            if state.observed_from is None or decision_at - state.observed_from < timedelta(
                seconds=self.configuration.features.warmup_seconds
            ):
                reasons.append("WARMUP")
            for window in windows:
                if window.trade_count < 2:
                    reasons.append(f"INSUFFICIENT_TRADES_{window.window_seconds}S")
                if window.quote_change_count < 1:
                    reasons.append(f"INSUFFICIENT_BOOK_FLOW_{window.window_seconds}S")

        latest_quote = state.latest_quote
        book = (
            book_values(latest_quote)
            if latest_quote is not None
            and latest_quote.is_complete
            and latest_quote.received_at <= decision_at
            else _EMPTY_BOOK
        )
        trade_date = decision_at.astimezone(self._timezone).date()
        return FeatureSnapshot(
            feature_version=self.configuration.features.version,
            configuration_version=self.configuration.version,
            configuration_sha256=self.configuration.sha256,
            symbol=symbol,
            trade_date=trade_date,
            decision_at=decision_at,
            market_session=session,
            stream_session_id=self._last_stream_session_id,
            last_receive_sequence=self._last_receive_sequence,
            trade_age_seconds=_age(
                decision_at,
                state.latest_trade.received_at if state.latest_trade is not None else None,
            ),
            quote_age_seconds=_age(
                decision_at,
                latest_quote.received_at if latest_quote is not None else None,
            ),
            windows=windows,
            reasons=tuple(dict.fromkeys(reasons)),
            **book,
        )

    def snapshots(self, decision_at: datetime) -> tuple[FeatureSnapshot, ...]:
        """Emit one feature vector per configured symbol at an aligned instant."""
        decision_at = _utc(decision_at, "decision_at")
        cadence = self.configuration.features.cadence_seconds
        if decision_at.microsecond or int(decision_at.timestamp()) % cadence:
            raise ValueError("decision_at must align to the configured feature cadence")
        if self._last_decision_at is not None and decision_at <= self._last_decision_at:
            raise ValueError("feature decision times must be strictly increasing")
        if self._last_received_at is not None and self._last_received_at > decision_at:
            raise ValueError("feature state contains input unavailable at decision_at")
        trade_date = decision_at.astimezone(self._timezone).date()
        if not self.configuration.contains(trade_date):
            raise ValueError("decision falls outside the configuration effective interval")
        session = session_at(
            decision_at,
            trade_date=trade_date,
            timezone=self._timezone,
            schedule=self.configuration.market.sessions,
        )
        snapshots = tuple(
            self._snapshot(symbol, decision_at=decision_at, session=session)
            for symbol in self.configuration.market.symbols
        )
        self._last_decision_at = decision_at
        return snapshots


def decision_times(configuration: TradingVersion, trade_date: date) -> Iterator[datetime]:
    """Yield configured decision instants without crossing disabled market sessions."""
    if not configuration.contains(trade_date):
        raise ValueError("trade_date falls outside the configuration effective interval")
    timezone = ZoneInfo(configuration.market.timezone)
    cadence = timedelta(seconds=configuration.features.cadence_seconds)
    for name in configuration.features.decision_sessions:
        start, stop = session_window(
            trade_date,
            MarketSession(name),
            timezone=timezone,
            schedule=configuration.market.sessions,
        )
        current = start + cadence
        while current < stop:
            yield current
            current += cadence


def replay_features(
    envelopes: Iterable[StreamEnvelope],
    configuration: TradingVersion,
    *,
    trade_date: date,
) -> tuple[FeatureSnapshot, ...]:
    """Replay receipt-ordered inputs through the same engine used by a live clock."""
    engine = FeatureEngine(configuration)
    iterator = iter(envelopes)
    pending = next(iterator, None)
    snapshots: list[FeatureSnapshot] = []
    for decision_at in decision_times(configuration, trade_date):
        while pending is not None and pending.received_at <= decision_at:
            engine.apply(pending)
            pending = next(iterator, None)
        snapshots.extend(engine.snapshots(decision_at))
    # Exhaust lazy readers so their terminal checksum and sequence validation runs even though
    # features intentionally stop before the closing auction.
    deque(iterator, maxlen=0)
    return tuple(snapshots)
