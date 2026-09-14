"""Reconcile one verified raw session with deterministic market replay."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, computed_field, model_validator
from ssi_sdk.models import IntervalMessage

from t0_trading.capture.reader import StreamDayReader, StreamGap, StreamSessionReader
from t0_trading.configuration import TradingVersion
from t0_trading.identity import canonical_json, sha256
from t0_trading.market.events import (
    MarketEvent,
    QuoteSnapshot,
    StreamEnvelope,
    Trade,
    provider_price,
    provider_timestamp,
)
from t0_trading.market.replay import replay
from t0_trading.market.session import (
    TRADE_SESSIONS,
    MarketSession,
    covers_trading_window,
    session_at,
    session_window,
    trading_window,
)
from t0_trading.market.state import Bar, bar_start

_INTERVAL_ADAPTER = TypeAdapter(IntervalMessage)
_BarKey = tuple[str, datetime]
_BarValues = tuple[Decimal, Decimal, Decimal, Decimal, int]
MarketDayFailureReason = Literal[
    "no_terminal_session",
    "no_full_session",
    "capture_scope_mismatch",
    "multiple_full_sessions",
    "overlapping_sessions",
    "reconciliation_failed",
]


@dataclass(frozen=True, slots=True)
class _ProviderBar:
    symbol: str
    start: datetime
    observed_at: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: int

    @property
    def values(self) -> _BarValues:
        return (
            self.open_price,
            self.high_price,
            self.low_price,
            self.close_price,
            self.volume,
        )


@dataclass(slots=True)
class _TradePrefix:
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: int

    @classmethod
    def from_trade(cls, trade: Trade) -> _TradePrefix:
        return cls(trade.price, trade.price, trade.price, trade.price, trade.quantity)

    def add(self, trade: Trade) -> None:
        self.high_price = max(self.high_price, trade.price)
        self.low_price = min(self.low_price, trade.price)
        self.close_price = trade.price
        self.volume += trade.quantity

    @property
    def values(self) -> _BarValues:
        return (
            self.open_price,
            self.high_price,
            self.low_price,
            self.close_price,
            self.volume,
        )


@dataclass(frozen=True, slots=True)
class _CaptureSet:
    manifest_count: int
    full_window_session_count: int
    selected: StreamDayReader | None
    gaps: tuple[StreamGap, ...]
    failure_reason: MarketDayFailureReason | None
    evidence_sha256: str


def _evidence_sha256(readers: Sequence[StreamSessionReader]) -> str:
    return sha256(canonical_json(sorted(reader.manifest_sha256 for reader in readers)))


class ReconciliationReport(BaseModel):
    """Stable JSON summary suitable for CI, operations, and audit history."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_uris: tuple[str, ...]
    stream_session_ids: tuple[str, ...]
    trade_date: str
    configuration_version: str
    configuration_sha256: str
    connected_at: datetime
    disconnected_at: datetime
    gap_count: int = Field(ge=0)
    gap_duration_milliseconds: int = Field(ge=0)
    full_session_coverage: bool
    capture_scope_matches_configuration: bool
    trade_symbols: tuple[str, ...]
    quote_symbols: tuple[str, ...]
    cumulative_volume_baseline_matches: bool
    out_of_session_trade_count: int
    input_count: int
    message_counts: dict[str, int]
    business_event_count: int
    event_counts: dict[str, int]
    session_counts: dict[str, int]
    replayed_bar_count: int
    provider_interval_update_count: int
    provider_interval_minute_count: int
    matched_interval_update_count: int
    gap_interval_update_count: int
    final_interval_exact_minute_count: int
    provider_interval_progression_issue_count: int
    differences: tuple[str, ...]
    replay_issues: tuple[str, ...]

    @model_validator(mode="after")
    def validate_totals(self) -> ReconciliationReport:
        if sum(self.message_counts.values()) != self.input_count:
            raise ValueError("message counts do not reconcile to input_count")
        if (
            sum(self.event_counts.values()) != self.business_event_count
            or sum(self.session_counts.values()) != self.business_event_count
        ):
            raise ValueError("event counts do not reconcile to business_event_count")
        if (
            self.provider_interval_update_count != self.message_counts.get("IntervalMessage", 0)
            or self.matched_interval_update_count + self.gap_interval_update_count
            > self.provider_interval_update_count
            or self.provider_interval_minute_count > self.provider_interval_update_count
            or self.final_interval_exact_minute_count > self.provider_interval_minute_count
            or self.provider_interval_progression_issue_count > self.provider_interval_update_count
            or (self.gap_count < 1 and self.gap_duration_milliseconds != 0)
        ):
            raise ValueError("provider interval counts are inconsistent")
        if (
            not self.manifest_uris
            or len(self.manifest_uris) != len(self.stream_session_ids)
            or len(set(self.stream_session_ids)) != len(self.stream_session_ids)
        ):
            raise ValueError("reconciliation capture lineage is inconsistent")
        if any(
            tuple(sorted(set(symbols))) != symbols
            for symbols in (self.trade_symbols, self.quote_symbols)
        ):
            raise ValueError("event symbol summaries must be sorted and unique")
        return self

    @computed_field
    @property
    def status(self) -> Literal["passed", "failed"]:
        """Derive the terminal outcome from one canonical set of gates."""
        passed = (
            self.full_session_coverage
            and self.capture_scope_matches_configuration
            and self.cumulative_volume_baseline_matches
            and self.out_of_session_trade_count == 0
            and self.business_event_count > 0
            and self.matched_interval_update_count + self.gap_interval_update_count
            == self.provider_interval_update_count
            and self.provider_interval_progression_issue_count == 0
            and not self.differences
            and not self.replay_issues
        )
        return "passed" if passed else "failed"


class MarketDayCertification(BaseModel):
    """Current deterministic backtest eligibility for one market day and configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trade_date: date
    configuration_version: str
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["passed", "failed"]
    failure_reason: MarketDayFailureReason | None
    manifest_count: int = Field(ge=0)
    full_window_session_count: int = Field(ge=0)
    eligible_session_count: int = Field(ge=0)
    selected_stream_session_id: str | None
    selected_stream_session_ids: tuple[str, ...]
    gaps: tuple[StreamGap, ...]
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_outcome(self) -> MarketDayCertification:
        if (
            self.full_window_session_count > self.manifest_count
            or self.eligible_session_count > self.manifest_count
        ):
            raise ValueError("market-day certification counts are inconsistent")
        passed = self.status == "passed"
        if (
            passed != (self.failure_reason is None)
            or passed != bool(self.selected_stream_session_ids)
            or (passed and self.eligible_session_count != len(self.selected_stream_session_ids))
            or (not passed and self.selected_stream_session_ids)
            or self.selected_stream_session_id
            != (
                self.selected_stream_session_ids[0]
                if len(self.selected_stream_session_ids) == 1
                else None
            )
        ):
            raise ValueError("market-day certification outcome is inconsistent")
        if len(set(self.selected_stream_session_ids)) != len(self.selected_stream_session_ids):
            raise ValueError("selected stream sessions must be unique")
        if not passed and self.gaps:
            raise ValueError("failed market-day certification cannot authorize capture gaps")
        if (
            (self.failure_reason == "no_terminal_session") != (self.manifest_count == 0)
            or (
                self.failure_reason == "multiple_full_sessions"
                and self.full_window_session_count < 2
            )
            or (self.failure_reason == "reconciliation_failed" and self.eligible_session_count < 1)
        ):
            raise ValueError("market-day certification reason is inconsistent")
        return self


def _provider_bar(envelope: StreamEnvelope, timezone: ZoneInfo) -> _ProviderBar | None:
    if envelope.message_type != "IntervalMessage":
        return None
    try:
        message = _INTERVAL_ADAPTER.validate_json(envelope.message_json)
    except ValueError as error:
        raise ValueError("invalid SSI IntervalMessage") from error
    if (
        envelope.symbol != message.symbol
        or envelope.source_time_text != message.trading_time
        or message.type.value != "trade"
        or message.volume <= 0
    ):
        raise ValueError("SSI IntervalMessage lineage is inconsistent")
    start = provider_timestamp(message.interval_time, timezone).astimezone(timezone)
    observed_at = provider_timestamp(message.trading_time, timezone).astimezone(timezone)
    prices = tuple(
        provider_price(value) for value in (message.open, message.high, message.low, message.close)
    )
    if (
        start.replace(second=0, microsecond=0) != start
        or not start <= observed_at < start + timedelta(minutes=1)
        or observed_at > envelope.received_at.astimezone(timezone)
    ):
        raise ValueError("SSI IntervalMessage timestamps are inconsistent")
    open_price, high_price, low_price, close_price = prices
    if (
        low_price <= 0
        or low_price > min(open_price, close_price)
        or high_price < max(open_price, close_price)
    ):
        raise ValueError("SSI IntervalMessage OHLC values are inconsistent")
    return _ProviderBar(
        symbol=message.symbol.upper(),
        start=start,
        observed_at=observed_at,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        volume=message.volume,
    )


def _bar_values(bar: Bar) -> _BarValues:
    return (
        bar.open_price,
        bar.high_price,
        bar.low_price,
        bar.close_price,
        bar.volume,
    )


def _interval_progression_issue(previous: _ProviderBar, current: _ProviderBar) -> str | None:
    if current.observed_at < previous.observed_at:
        return "observed_time_regression"
    if current.volume < previous.volume:
        return "volume_regression"
    if current.volume == previous.volume:
        return None if current.values == previous.values else "ohlc_mutation_without_volume"
    if (
        current.open_price != previous.open_price
        or current.high_price < previous.high_price
        or current.low_price > previous.low_price
    ):
        return "ohlc_regression"
    return None


def _capture_set(
    readers: Sequence[StreamSessionReader],
    configuration: TradingVersion,
    *,
    trade_date: date,
) -> _CaptureSet:
    if not configuration.contains(trade_date):
        raise ValueError("configuration is not effective for the captured trade date")
    if any(reader.trade_date != trade_date for reader in readers):
        raise ValueError("SSI Stream session escaped the requested trading date")
    timezone = ZoneInfo(configuration.market.timezone)
    market_open, market_close = trading_window(
        trade_date,
        timezone=timezone,
        schedule=configuration.market.sessions,
    )
    ready = tuple(reader for reader in readers if reader.manifest.heartbeat_count > 0)
    full_window = tuple(
        reader
        for reader in ready
        if covers_trading_window(
            reader.manifest.connected_at,
            reader.covered_until,
            trade_date=trade_date,
            timezone=timezone,
            schedule=configuration.market.sessions,
        )
    )
    configured_symbols = set(configuration.market.symbols)
    scoped_manifests = tuple(
        reader for reader in readers if set(reader.manifest.symbols) == configured_symbols
    )
    scoped = tuple(reader for reader in ready if reader in scoped_manifests)
    eligible_full = tuple(reader for reader in full_window if reader in scoped)
    selected: StreamDayReader | None = None
    if not readers:
        failure_reason: MarketDayFailureReason | None = "no_terminal_session"
    elif len(eligible_full) > 1:
        failure_reason = "multiple_full_sessions"
    elif len(eligible_full) == 1:
        selected = StreamDayReader(eligible_full)
        failure_reason = None
    elif not scoped_manifests:
        failure_reason = "capture_scope_mismatch"
    elif not scoped:
        failure_reason = "no_full_session"
    else:
        relevant = tuple(
            reader
            for reader in scoped
            if reader.manifest.connected_at <= market_close and reader.covered_until >= market_open
        )
        if (
            not relevant
            or min(reader.manifest.connected_at for reader in relevant) > market_open
            or max(reader.covered_until for reader in relevant) < market_close
        ):
            failure_reason = "no_full_session"
        else:
            try:
                selected = StreamDayReader(relevant)
            except ValueError:
                failure_reason = "overlapping_sessions"
            else:
                failure_reason = None
    gaps = _active_gaps(selected, configuration) if selected is not None else ()
    return _CaptureSet(
        manifest_count=len(readers),
        full_window_session_count=len(full_window),
        selected=selected,
        gaps=gaps,
        failure_reason=failure_reason,
        evidence_sha256=_evidence_sha256(readers),
    )


def _active_gaps(
    capture: StreamDayReader,
    configuration: TradingVersion,
) -> tuple[StreamGap, ...]:
    timezone = ZoneInfo(configuration.market.timezone)
    windows = tuple(
        session_window(
            capture.trade_date,
            session,
            timezone=timezone,
            schedule=configuration.market.sessions,
        )
        for session in (
            MarketSession.OPENING_AUCTION,
            MarketSession.CONTINUOUS_AM,
            MarketSession.CONTINUOUS_PM,
            MarketSession.CLOSING_AUCTION,
        )
    )
    return tuple(
        StreamGap(started_at=max(gap.started_at, start), ended_at=min(gap.ended_at, end))
        for gap in capture.gaps
        for start, end in windows
        if gap.started_at < end and gap.ended_at > start
    )


def certify_market_day(
    readers: Sequence[StreamSessionReader],
    configuration: TradingVersion,
    *,
    trade_date: date,
) -> tuple[MarketDayCertification, ReconciliationReport | None]:
    """Certify one logical market-day capture assembled from immutable segments."""
    capture_set = _capture_set(readers, configuration, trade_date=trade_date)
    candidate = capture_set.selected
    report = (
        reconcile_capture(candidate, configuration, gaps=capture_set.gaps)
        if candidate is not None
        else None
    )
    failure_reason = capture_set.failure_reason
    if report is not None and report.status != "passed":
        failure_reason = "reconciliation_failed"
    selected_ids = candidate.stream_session_ids if failure_reason is None and candidate else ()
    return MarketDayCertification(
        trade_date=trade_date,
        configuration_version=configuration.version,
        configuration_sha256=configuration.sha256,
        status="passed" if selected_ids else "failed",
        failure_reason=failure_reason,
        manifest_count=capture_set.manifest_count,
        full_window_session_count=capture_set.full_window_session_count,
        eligible_session_count=len(candidate.sessions) if candidate is not None else 0,
        selected_stream_session_id=selected_ids[0] if len(selected_ids) == 1 else None,
        selected_stream_session_ids=selected_ids,
        gaps=capture_set.gaps,
        evidence_sha256=capture_set.evidence_sha256,
    ), report


def reconcile_trade_date(
    readers: Sequence[StreamSessionReader],
    configuration: TradingVersion,
    *,
    trade_date: date,
) -> ReconciliationReport:
    """Select and certify the logical capture covering the configured market window."""
    certification, report = certify_market_day(readers, configuration, trade_date=trade_date)
    if report is None:
        if certification.failure_reason == "no_terminal_session":
            raise ValueError("no terminal SSI Stream session exists for the trading date")
        raise ValueError(
            "no unambiguous SSI Stream segment chain covers the market window and symbol scope"
        )
    return report


def select_feature_capture(
    readers: Sequence[StreamSessionReader],
    certification: MarketDayCertification,
) -> StreamDayReader:
    """Rebuild the exact logical capture authorized by an existing certification."""
    if certification.failure_reason == "no_terminal_session":
        raise ValueError("no terminal SSI Stream session exists for the trading date")
    if certification.status != "passed":
        if certification.failure_reason == "reconciliation_failed":
            raise ValueError("SSI Stream session reconciliation failed")
        raise ValueError(
            "no unambiguous SSI Stream segment chain covers the market window and symbol scope"
        )
    by_id = {reader.manifest.stream_session_id: reader for reader in readers}
    if (
        len(by_id) != len(readers)
        or _evidence_sha256(readers) != certification.evidence_sha256
        or any(session_id not in by_id for session_id in certification.selected_stream_session_ids)
    ):
        raise ValueError("certified SSI Stream evidence is not reproducible")
    selected = StreamDayReader(
        tuple(by_id[session_id] for session_id in certification.selected_stream_session_ids)
    )
    if (
        selected.trade_date != certification.trade_date
        or selected.stream_session_ids != certification.selected_stream_session_ids
    ):
        raise ValueError("certified SSI Stream capture selection is not reproducible")
    return selected


def reconcile_capture(
    capture: StreamDayReader,
    configuration: TradingVersion,
    *,
    gaps: Sequence[StreamGap] = (),
) -> ReconciliationReport:
    """Read one logical day and reconcile provider intervals outside known gaps."""
    if not configuration.contains(capture.trade_date):
        raise ValueError("configuration is not effective for the captured trade date")
    timezone = ZoneInfo(configuration.market.timezone)
    message_counts: Counter[str] = Counter()
    provider_bars: dict[_BarKey, _ProviderBar] = {}
    provider_updates: list[_ProviderBar] = []
    trade_prefixes: dict[_BarKey, _TradePrefix] = {}
    observed_prefixes: dict[_BarKey, dict[_BarValues, datetime]] = {}
    trade_symbols: set[str] = set()
    quote_symbols: set[str] = set()
    first_trades: dict[str, Trade] = {}
    out_of_session_trades: Counter[str] = Counter()
    differences: set[str] = set()
    provider_interval_progression_issue_count = 0
    interval_seconds = configuration.market.bar_interval_seconds

    def observe_event(event: MarketEvent) -> None:
        if isinstance(event, QuoteSnapshot):
            quote_symbols.add(event.symbol)
            return
        if not isinstance(event, Trade):
            return
        trade_symbols.add(event.symbol)
        first_trades.setdefault(event.symbol, event)
        session = session_at(
            event.event_time,
            trade_date=capture.trade_date,
            timezone=timezone,
            schedule=configuration.market.sessions,
        )
        if session not in TRADE_SESSIONS:
            out_of_session_trades[session.value] += 1
        key = (
            event.symbol,
            bar_start(event.event_time, interval_seconds).astimezone(timezone),
        )
        prefix = trade_prefixes.get(key)
        if prefix is None:
            prefix = _TradePrefix.from_trade(event)
            trade_prefixes[key] = prefix
        else:
            prefix.add(event)
        observed_prefixes.setdefault(key, {})[prefix.values] = event.event_time.astimezone(timezone)

    def observed() -> Iterable[StreamEnvelope]:
        nonlocal provider_interval_progression_issue_count
        for envelope in capture.envelopes():
            message_counts[envelope.message_type] += 1
            interval = _provider_bar(envelope, timezone)
            if interval is not None:
                if interval.symbol not in configuration.market.symbols:
                    raise ValueError("SSI interval symbol is outside the configured universe")
                if interval.start.date() != capture.trade_date:
                    raise ValueError("SSI interval does not belong to the captured trade date")
                key = (interval.symbol, interval.start)
                current = provider_bars.get(key)
                issue = (
                    _interval_progression_issue(current, interval) if current is not None else None
                )
                affected = any(
                    gap.overlaps(
                        interval.start,
                        interval.start + timedelta(seconds=interval_seconds),
                    )
                    for gap in gaps
                )
                if issue is None or affected:
                    provider_bars[key] = interval
                else:
                    provider_interval_progression_issue_count += 1
                    differences.add(
                        f"{interval.symbol}@{interval.start.isoformat()}:"
                        f"interval_{issue}[observed_at={interval.observed_at.isoformat()}]"
                    )
                provider_updates.append(interval)
            yield envelope

    result = replay(
        observed(),
        configuration,
        trade_date=capture.trade_date,
        finish_at=capture.disconnected_at,
        observe=observe_event,
    )
    replayed_bars = {
        (bar.symbol, bar.start.astimezone(timezone)): bar for bar in result.finalized_bars
    }
    provider_keys = set(provider_bars)
    replay_keys = set(replayed_bars)
    final_exact = 0
    for symbol, start in sorted(provider_keys | replay_keys):
        if any(gap.overlaps(start, start + timedelta(seconds=interval_seconds)) for gap in gaps):
            continue
        label = f"{symbol}@{start.isoformat()}"
        provider = provider_bars.get((symbol, start))
        reconstructed = replayed_bars.get((symbol, start))
        if provider is None:
            differences.add(f"{label}:missing_provider_interval")
        elif reconstructed is None:
            differences.add(f"{label}:missing_replayed_bar")
        elif provider.values == _bar_values(reconstructed):
            final_exact += 1

    matched_updates = 0
    gap_updates = 0
    for interval in provider_updates:
        key = (interval.symbol, interval.start)
        if any(
            gap.overlaps(
                interval.start,
                interval.start + timedelta(seconds=interval_seconds),
            )
            for gap in gaps
        ):
            gap_updates += 1
            continue
        prefix_time = observed_prefixes.get(key, {}).get(interval.values)
        if prefix_time is not None and prefix_time <= interval.observed_at:
            matched_updates += 1
        else:
            symbol, start = key
            differences.add(
                f"{symbol}@{start.isoformat()}:interval_not_causal_trade_prefix["
                f"observed_at={interval.observed_at.isoformat()}]"
            )

    full_session_coverage = covers_trading_window(
        capture.connected_at,
        capture.covered_until,
        trade_date=capture.trade_date,
        timezone=timezone,
        schedule=configuration.market.sessions,
    )
    configured_symbols = set(configuration.market.symbols)
    capture_scope_matches_configuration = set(capture.symbols) == configured_symbols
    for symbol in sorted(configured_symbols - trade_symbols):
        differences.add(f"{symbol}:configured_symbol_without_trade")
    for symbol in sorted(configured_symbols - quote_symbols):
        differences.add(f"{symbol}:configured_symbol_without_quote")
    cumulative_volume_baseline_matches = all(
        trade.cumulative_volume == trade.quantity for trade in first_trades.values()
    )
    for symbol, trade in sorted(first_trades.items()):
        if trade.cumulative_volume != trade.quantity:
            differences.add(f"{symbol}:first_trade_cumulative_volume_mismatch")
    out_of_session_trade_count = sum(out_of_session_trades.values())
    for session, count in sorted(out_of_session_trades.items()):
        differences.add(f"trade_outside_session[{session}]={count}")
    return ReconciliationReport(
        manifest_uris=capture.manifest_uris,
        stream_session_ids=capture.stream_session_ids,
        trade_date=capture.trade_date.isoformat(),
        configuration_version=configuration.version,
        configuration_sha256=configuration.sha256,
        connected_at=capture.connected_at,
        disconnected_at=capture.disconnected_at,
        gap_count=len(gaps),
        gap_duration_milliseconds=sum(gap.duration_milliseconds for gap in gaps),
        full_session_coverage=full_session_coverage,
        capture_scope_matches_configuration=capture_scope_matches_configuration,
        trade_symbols=tuple(sorted(trade_symbols)),
        quote_symbols=tuple(sorted(quote_symbols)),
        cumulative_volume_baseline_matches=cumulative_volume_baseline_matches,
        out_of_session_trade_count=out_of_session_trade_count,
        input_count=result.input_count,
        message_counts=dict(sorted(message_counts.items())),
        business_event_count=result.business_event_count,
        event_counts=dict(result.event_counts),
        session_counts=dict(result.session_counts),
        replayed_bar_count=len(replayed_bars),
        provider_interval_update_count=len(provider_updates),
        provider_interval_minute_count=len(provider_bars),
        matched_interval_update_count=matched_updates,
        gap_interval_update_count=gap_updates,
        final_interval_exact_minute_count=final_exact,
        provider_interval_progression_issue_count=provider_interval_progression_issue_count,
        differences=tuple(sorted(differences)),
        replay_issues=result.issues,
    )


def reconcile_session(
    reader: StreamSessionReader,
    configuration: TradingVersion,
) -> ReconciliationReport:
    """Reconcile one transport segment through the logical-day implementation."""
    return reconcile_capture(StreamDayReader((reader,)), configuration)
