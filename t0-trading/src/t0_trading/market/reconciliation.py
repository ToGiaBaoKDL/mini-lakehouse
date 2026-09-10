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

from t0_trading.capture.reader import StreamDisconnectKind, StreamSessionReader
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
    covers_trading_window,
    session_at,
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
    eligible: tuple[StreamSessionReader, ...]
    failure_reason: MarketDayFailureReason | None
    evidence_sha256: str


class ReconciliationReport(BaseModel):
    """Stable JSON summary suitable for CI, operations, and audit history."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_uri: str
    stream_session_id: str
    trade_date: str
    configuration_version: str
    configuration_sha256: str
    connected_at: datetime
    disconnected_at: datetime
    disconnect_kind: StreamDisconnectKind
    error_type: str | None
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
            or self.matched_interval_update_count > self.provider_interval_update_count
            or self.provider_interval_minute_count > self.provider_interval_update_count
            or self.final_interval_exact_minute_count > self.provider_interval_minute_count
            or self.provider_interval_progression_issue_count > self.provider_interval_update_count
        ):
            raise ValueError("provider interval counts are inconsistent")
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
            and self.matched_interval_update_count == self.provider_interval_update_count
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
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_outcome(self) -> MarketDayCertification:
        if (
            self.full_window_session_count > self.manifest_count
            or self.eligible_session_count > self.full_window_session_count
        ):
            raise ValueError("market-day certification counts are inconsistent")
        passed = self.status == "passed"
        if (
            passed != (self.failure_reason is None)
            or passed != (self.selected_stream_session_id is not None)
            or (passed and self.eligible_session_count != 1)
        ):
            raise ValueError("market-day certification outcome is inconsistent")
        selection_reason: MarketDayFailureReason | None
        if self.manifest_count == 0:
            selection_reason = "no_terminal_session"
        elif self.full_window_session_count == 0:
            selection_reason = "no_full_session"
        elif self.eligible_session_count == 0:
            selection_reason = "capture_scope_mismatch"
        elif self.eligible_session_count > 1:
            selection_reason = "multiple_full_sessions"
        else:
            selection_reason = None
        allowed_reasons = (
            {selection_reason} if selection_reason is not None else {None, "reconciliation_failed"}
        )
        if self.failure_reason not in allowed_reasons:
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
    full_window = tuple(
        reader
        for reader in readers
        if covers_trading_window(
            reader.manifest.connected_at,
            reader.manifest.disconnected_at,
            trade_date=trade_date,
            timezone=timezone,
            schedule=configuration.market.sessions,
        )
    )
    eligible = tuple(
        reader
        for reader in full_window
        if set(reader.manifest.symbols) == set(configuration.market.symbols)
    )
    if not readers:
        failure_reason: MarketDayFailureReason | None = "no_terminal_session"
    elif not full_window:
        failure_reason = "no_full_session"
    elif not eligible:
        failure_reason = "capture_scope_mismatch"
    elif len(eligible) > 1:
        failure_reason = "multiple_full_sessions"
    else:
        failure_reason = None
    return _CaptureSet(
        manifest_count=len(readers),
        full_window_session_count=len(full_window),
        eligible=eligible,
        failure_reason=failure_reason,
        evidence_sha256=sha256(
            canonical_json(sorted(reader.manifest_sha256 for reader in readers))
        ),
    )


def certify_market_day(
    readers: Sequence[StreamSessionReader],
    configuration: TradingVersion,
    *,
    trade_date: date,
) -> tuple[MarketDayCertification, ReconciliationReport | None]:
    """Certify the sole full capture and return its detailed reconciliation when available."""
    capture_set = _capture_set(readers, configuration, trade_date=trade_date)
    candidate = next(iter(capture_set.eligible)) if len(capture_set.eligible) == 1 else None
    report = reconcile_session(candidate, configuration) if candidate is not None else None
    failure_reason = capture_set.failure_reason
    if report is not None and report.status != "passed":
        failure_reason = "reconciliation_failed"
    selected = candidate if failure_reason is None else None
    return MarketDayCertification(
        trade_date=trade_date,
        configuration_version=configuration.version,
        configuration_sha256=configuration.sha256,
        status="passed" if selected is not None else "failed",
        failure_reason=failure_reason,
        manifest_count=capture_set.manifest_count,
        full_window_session_count=capture_set.full_window_session_count,
        eligible_session_count=len(capture_set.eligible),
        selected_stream_session_id=(
            selected.manifest.stream_session_id if selected is not None else None
        ),
        evidence_sha256=capture_set.evidence_sha256,
    ), report


def reconcile_trade_date(
    readers: Sequence[StreamSessionReader],
    configuration: TradingVersion,
    *,
    trade_date: date,
) -> ReconciliationReport:
    """Select and certify the sole capture covering the configured market window."""
    certification, report = certify_market_day(readers, configuration, trade_date=trade_date)
    if report is None:
        if certification.failure_reason == "no_terminal_session":
            raise ValueError("no terminal SSI Stream session exists for the trading date")
        raise ValueError(
            "expected exactly one SSI Stream session covering the market window and symbol scope, "
            f"found {certification.eligible_session_count}"
        )
    return report


def select_feature_capture(
    readers: Sequence[StreamSessionReader],
    configuration: TradingVersion,
    *,
    trade_date: date,
) -> StreamSessionReader:
    """Select exactly one fully reconciled capture for deterministic feature replay."""
    certification, _ = certify_market_day(readers, configuration, trade_date=trade_date)
    if certification.failure_reason == "no_terminal_session":
        raise ValueError("no terminal SSI Stream session exists for the trading date")
    if certification.status != "passed":
        if certification.failure_reason == "reconciliation_failed":
            raise ValueError("SSI Stream session reconciliation failed")
        raise ValueError(
            "expected exactly one SSI Stream session covering the market window and symbol scope, "
            f"found {certification.eligible_session_count}"
        )
    return next(
        reader
        for reader in readers
        if reader.manifest.stream_session_id == certification.selected_stream_session_id
    )


def reconcile_session(
    reader: StreamSessionReader,
    configuration: TradingVersion,
) -> ReconciliationReport:
    """Read once, replay once, and match provider intervals to observed trade prefixes."""
    if not configuration.contains(reader.trade_date):
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
            trade_date=reader.trade_date,
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
        for envelope in reader.envelopes():
            message_counts[envelope.message_type] += 1
            interval = _provider_bar(envelope, timezone)
            if interval is not None:
                if interval.symbol not in configuration.market.symbols:
                    raise ValueError("SSI interval symbol is outside the configured universe")
                if interval.start.date() != reader.trade_date:
                    raise ValueError("SSI interval does not belong to the captured trade date")
                key = (interval.symbol, interval.start)
                current = provider_bars.get(key)
                issue = (
                    _interval_progression_issue(current, interval) if current is not None else None
                )
                if issue is None:
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
        trade_date=reader.trade_date,
        finish_at=reader.manifest.disconnected_at,
        observe=observe_event,
    )
    replayed_bars = {
        (bar.symbol, bar.start.astimezone(timezone)): bar for bar in result.finalized_bars
    }
    provider_keys = set(provider_bars)
    replay_keys = set(replayed_bars)
    final_exact = 0
    for symbol, start in sorted(provider_keys | replay_keys):
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
    for interval in provider_updates:
        key = (interval.symbol, interval.start)
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
        reader.manifest.connected_at,
        reader.manifest.disconnected_at,
        trade_date=reader.trade_date,
        timezone=timezone,
        schedule=configuration.market.sessions,
    )
    configured_symbols = set(configuration.market.symbols)
    capture_scope_matches_configuration = set(reader.manifest.symbols) == configured_symbols
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
        manifest_uri=reader.uri,
        stream_session_id=reader.manifest.stream_session_id,
        trade_date=reader.trade_date.isoformat(),
        configuration_version=configuration.version,
        configuration_sha256=configuration.sha256,
        connected_at=reader.manifest.connected_at,
        disconnected_at=reader.manifest.disconnected_at,
        disconnect_kind=reader.manifest.disconnect_kind,
        error_type=reader.manifest.error_type,
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
        final_interval_exact_minute_count=final_exact,
        provider_interval_progression_issue_count=provider_interval_progression_issue_count,
        differences=tuple(sorted(differences)),
        replay_issues=result.issues,
    )
