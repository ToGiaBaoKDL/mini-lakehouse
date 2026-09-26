"""Deterministic point-in-time zone, index confirmation, and regime construction."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter
from ssi_sdk.models import MarketStatusMessage, TradeMessage

from t0_trading.configuration import ContextVersion, TradingVersion
from t0_trading.context.model import (
    ContextDataMode,
    DecisionContext,
    IndexContext,
    IndexSourceKind,
    MarketRegime,
    MarketStatusContext,
    MarketStatusSourceKind,
    MarketWindowContext,
    ZoneContext,
)
from t0_trading.features import FeatureSnapshot
from t0_trading.identity import canonical_json, sha256
from t0_trading.market.events import StreamEnvelope, provider_timestamp
from t0_trading.numeric import RATIO_QUANTUM, basis_points, ratio

_TRADE_ADAPTER = TypeAdapter(TradeMessage)
_MARKET_STATUS_ADAPTER = TypeAdapter(MarketStatusMessage)
_ZERO = Decimal(0)
_ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class IndexObservation:
    """One index value with an explicit point-in-time source boundary."""

    index: str
    value: Decimal
    observed_at: datetime
    source_kind: IndexSourceKind
    source_record_sha256: str
    stream_session_id: str | None = None
    receive_sequence: int | None = None

    def __post_init__(self) -> None:
        if (
            self.index != self.index.strip().upper()
            or self.value <= 0
            or not self.value.is_finite()
        ):
            raise ValueError("invalid index observation")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("index observation time must be timezone-aware")
        if (self.stream_session_id is None) != (self.receive_sequence is None):
            raise ValueError("index stream position must be wholly present or absent")
        if self.source_kind == "ssi_stream_trade" and self.stream_session_id is None:
            raise ValueError("live index observation requires stream position lineage")
        if self.source_kind != "ssi_stream_trade" and self.stream_session_id is not None:
            raise ValueError("historical index observation cannot carry stream position lineage")
        if len(self.source_record_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.source_record_sha256
        ):
            raise ValueError("index source fingerprint must be a lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class MarketStatusObservation:
    market: str
    status: str
    observed_at: datetime
    source_kind: MarketStatusSourceKind
    source_record_sha256: str
    stream_session_id: str | None = None
    receive_sequence: int | None = None

    def __post_init__(self) -> None:
        if (
            self.market != self.market.strip().upper()
            or self.status != self.status.strip().upper()
            or not self.market
            or not self.status
        ):
            raise ValueError("invalid market-status observation")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("market-status observation time must be timezone-aware")
        if (self.stream_session_id is None) != (self.receive_sequence is None):
            raise ValueError("market-status stream position must be wholly present or absent")
        if self.source_kind == "ssi_stream_market_status" and self.stream_session_id is None:
            raise ValueError("live market-status observation requires stream lineage")
        if self.source_kind != "ssi_stream_market_status" and self.stream_session_id is not None:
            raise ValueError("calendar market-status observation cannot carry stream lineage")
        if len(self.source_record_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.source_record_sha256
        ):
            raise ValueError("market-status source fingerprint must be a lowercase SHA-256")


def _index_tick(
    envelope: StreamEnvelope, indices: set[str], timezone: ZoneInfo
) -> IndexObservation | None:
    if (
        envelope.subscription_context != "indices"
        or envelope.message_type != "TradeMessage"
        or envelope.symbol not in indices
    ):
        return None
    if sha256(envelope.message_json.encode()) != envelope.message_sha256:
        raise ValueError("index message checksum does not match its envelope")
    try:
        payload = _TRADE_ADAPTER.validate_json(envelope.message_json)
        value = Decimal(str(payload.price))
    except ValueError as error:
        raise ValueError("invalid index stream message") from error
    if payload.symbol.upper() != envelope.symbol or value <= 0 or not value.is_finite():
        raise ValueError("index stream message does not match its capture envelope")
    event_time = provider_timestamp(payload.trading_time, timezone)
    if event_time > envelope.received_at:
        raise ValueError("index event time follows receipt time")
    return IndexObservation(
        index=envelope.symbol,
        value=value,
        observed_at=envelope.received_at,
        source_kind="ssi_stream_trade",
        source_record_sha256=envelope.message_sha256,
        stream_session_id=envelope.stream_session_id,
        receive_sequence=envelope.receive_sequence,
    )


def _market_status_tick(
    envelope: StreamEnvelope, markets: set[str]
) -> MarketStatusObservation | None:
    if (
        envelope.subscription_context != "markets"
        or envelope.message_type != "MarketStatusMessage"
        or envelope.symbol not in markets
    ):
        return None
    if sha256(envelope.message_json.encode()) != envelope.message_sha256:
        raise ValueError("market-status checksum does not match its envelope")
    try:
        payload = _MARKET_STATUS_ADAPTER.validate_json(envelope.message_json)
    except ValueError as error:
        raise ValueError("invalid market-status stream message") from error
    market = payload.market.strip().upper()
    status = payload.status.strip().upper()
    if market != envelope.symbol or not status:
        raise ValueError("market-status message does not match its capture envelope")
    return MarketStatusObservation(
        market=market,
        status=status,
        observed_at=envelope.received_at,
        source_kind="ssi_stream_market_status",
        source_record_sha256=envelope.message_sha256,
        stream_session_id=envelope.stream_session_id,
        receive_sequence=envelope.receive_sequence,
    )


def _zone(
    current: FeatureSnapshot,
    history: Sequence[FeatureSnapshot],
    policy: ContextVersion,
) -> ZoneContext:
    prior = tuple(
        item
        for item in history
        if item.market_session == current.market_session
        and current.decision_at - timedelta(seconds=policy.zone_lookback_seconds)
        <= item.decision_at
        < current.decision_at
        and item.mid_price is not None
        and item.is_eligible
    )
    if current.mid_price is None or len(prior) < policy.zone_minimum_observations:
        return ZoneContext(
            symbol=current.symbol,
            current_feature_snapshot_sha256=current.sha256,
            observation_count=len(prior),
            support_distance_bps=None,
            resistance_distance_bps=None,
        )
    prices = tuple(item.mid_price for item in prior if item.mid_price is not None)
    support, resistance = min(prices), max(prices)
    support_distance = basis_points(current.mid_price - support, support)
    resistance_distance = basis_points(resistance - current.mid_price, resistance)
    strength = max(
        _ZERO,
        _ONE - ratio(abs(support_distance), policy.zone_tolerance_bps, quantum=RATIO_QUANTUM),
    )
    return ZoneContext(
        symbol=current.symbol,
        current_feature_snapshot_sha256=current.sha256,
        history_sha256=sha256(canonical_json([item.sha256 for item in prior])),
        observation_count=len(prior),
        support=support,
        resistance=resistance,
        support_distance_bps=support_distance,
        resistance_distance_bps=resistance_distance,
        near_support_strength=strength,
    )


def _as_of(ticks: Sequence[IndexObservation], at: datetime) -> IndexObservation | None:
    return next((tick for tick in reversed(ticks) if tick.observed_at <= at), None)


def _window(
    ticks: Sequence[IndexObservation],
    current: IndexObservation,
    decision_at: datetime,
    seconds: int,
    *,
    stale_after_seconds: int,
) -> MarketWindowContext | None:
    cutoff = decision_at - timedelta(seconds=seconds)
    reference = _as_of(ticks, cutoff)
    if reference is None or (cutoff - reference.observed_at).total_seconds() > stale_after_seconds:
        return None
    observed = [reference]
    observed.extend(tick for tick in ticks if cutoff < tick.observed_at <= current.observed_at)
    returns = [
        basis_points(item.value - previous.value, previous.value)
        for previous, item in pairwise(observed)
    ]
    if not returns:
        return None
    realized_variance = sum((value * value for value in returns), _ZERO)
    return MarketWindowContext(
        window_seconds=seconds,
        return_bps=basis_points(current.value - reference.value, reference.value),
        realized_volatility_bps=realized_variance.sqrt(),
    )


def _index_context(
    index: str,
    ticks: Sequence[IndexObservation],
    decision_at: datetime,
    policy: ContextVersion,
    *,
    stale_after_seconds: int,
) -> IndexContext:
    current = _as_of(ticks, decision_at)
    if current is None:
        return IndexContext(index=index, windows=(), reasons=("MISSING_INDEX",))
    age = Decimal(str((decision_at - current.observed_at).total_seconds()))
    windows = tuple(
        window
        for seconds in policy.market_windows_seconds
        if (
            window := _window(
                ticks,
                current,
                decision_at,
                seconds,
                stale_after_seconds=stale_after_seconds,
            )
        )
        is not None
    )
    reasons: list[str] = []
    if age > stale_after_seconds:
        reasons.append("STALE_INDEX")
    if tuple(window.window_seconds for window in windows) != policy.market_windows_seconds:
        reasons.append("INSUFFICIENT_INDEX_HISTORY")
    return IndexContext(
        index=index,
        value=current.value,
        age_seconds=age,
        source_kind=current.source_kind,
        source_record_sha256=current.source_record_sha256,
        stream_session_id=current.stream_session_id,
        receive_sequence=current.receive_sequence,
        windows=windows,
        reasons=tuple(reasons),
    )


def _market_status_context(
    market: str,
    observations: Sequence[MarketStatusObservation],
    decision_at: datetime,
    policy: ContextVersion,
) -> MarketStatusContext:
    current = next(
        (item for item in reversed(observations) if item.observed_at <= decision_at),
        None,
    )
    if current is None:
        return MarketStatusContext(market=market, reasons=("MISSING_MARKET_STATUS",))
    age = Decimal(str((decision_at - current.observed_at).total_seconds()))
    reasons: list[str] = []
    if current.status not in policy.tradable_market_statuses:
        reasons.append("MARKET_NOT_TRADABLE")
    return MarketStatusContext(
        market=market,
        status=current.status,
        age_seconds=age,
        is_tradable=not reasons,
        source_kind=current.source_kind,
        source_record_sha256=current.source_record_sha256,
        stream_session_id=current.stream_session_id,
        receive_sequence=current.receive_sequence,
        reasons=tuple(reasons),
    )


def _market_state(
    indices: Sequence[IndexContext],
    statuses: Sequence[MarketStatusContext],
    policy: ContextVersion,
) -> tuple[Decimal | None, MarketRegime, tuple[str, ...]]:
    reasons = tuple(
        dict.fromkeys(
            (
                *(f"{item.index}_{reason}" for item in indices for reason in item.reasons),
                *(f"{item.market}_{reason}" for item in statuses for reason in item.reasons),
            )
        )
    )
    if reasons:
        return None, "UNKNOWN", reasons
    long_window = policy.market_windows_seconds[-1]
    long = [
        next(window for window in item.windows if window.window_seconds == long_window)
        for item in indices
    ]
    confirmation = min(
        _ONE,
        max(
            _ZERO,
            min(
                ratio(window.return_bps, policy.trend_threshold_bps, quantum=RATIO_QUANTUM)
                for window in long
            ),
        ),
    )
    market_return = sum((window.return_bps for window in long), _ZERO) / len(long)
    market_volatility = sum((window.realized_volatility_bps for window in long), _ZERO) / len(long)
    regime: MarketRegime
    if market_volatility >= policy.high_volatility_threshold_bps:
        regime = "HIGH_VOLATILITY"
    elif market_return >= policy.trend_threshold_bps:
        regime = "TREND_UP"
    elif market_return <= -policy.trend_threshold_bps:
        regime = "TREND_DOWN"
    else:
        regime = "RANGE"
    return confirmation, regime, ()


def build_decision_contexts_from_observations(
    snapshots: Sequence[FeatureSnapshot],
    observations: Iterable[IndexObservation],
    configuration: TradingVersion,
    policy: ContextVersion,
    *,
    data_mode: ContextDataMode,
    market_status_observations: Iterable[MarketStatusObservation] = (),
) -> tuple[DecisionContext, ...]:
    """Build one context per feature clock from explicit as-of observations."""
    if data_mode not in {"LIVE", "HISTORICAL_PROXY"}:
        raise ValueError("unsupported context data mode")
    if not snapshots:
        raise ValueError("decision context requires feature snapshots")
    clocks: dict[datetime, list[FeatureSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        clocks[snapshot.decision_at].append(snapshot)
    expected_symbols = tuple(sorted(configuration.market.symbols))
    if any(
        tuple(sorted(item.symbol for item in group)) != expected_symbols
        or len({item.configuration_sha256 for item in group}) != 1
        for group in clocks.values()
    ):
        raise ValueError("decision context requires the complete feature matrix")
    trade_dates = {item.trade_date for item in snapshots}
    if len(trade_dates) != 1 or not policy.contains(next(iter(trade_dates))):
        raise ValueError("context policy must cover the feature trade date")

    index_ticks: dict[str, list[IndexObservation]] = {
        index: [] for index in configuration.market.indices
    }
    last_observed_at: datetime | None = None
    expected_source_kind: IndexSourceKind = (
        "ssi_stream_trade" if data_mode == "LIVE" else "ssi_rest_index_1m_historical"
    )
    for observation in observations:
        if observation.source_kind != expected_source_kind:
            raise ValueError("index observation source does not match context data mode")
        if last_observed_at is not None and observation.observed_at < last_observed_at:
            raise ValueError("index observations must be time ordered")
        last_observed_at = observation.observed_at
        if observation.index in index_ticks:
            index_ticks[observation.index].append(observation)

    status_ticks: dict[str, list[MarketStatusObservation]] = {
        market: [] for market in configuration.market.status_markets
    }
    last_status_at: datetime | None = None
    for observation in market_status_observations:
        if data_mode != "LIVE" or observation.source_kind != "ssi_stream_market_status":
            raise ValueError("market-status source does not match context data mode")
        if last_status_at is not None and observation.observed_at < last_status_at:
            raise ValueError("market-status observations must be time ordered")
        last_status_at = observation.observed_at
        if observation.market in status_ticks:
            status_ticks[observation.market].append(observation)

    history: dict[str, deque[FeatureSnapshot]] = {
        symbol: deque() for symbol in configuration.market.symbols
    }
    contexts: list[DecisionContext] = []
    stale_after_seconds = (
        policy.historical_proxy_stale_after_seconds
        if data_mode == "HISTORICAL_PROXY"
        else policy.index_stale_after_seconds
    )
    for decision_at, group in sorted(clocks.items()):
        zones = tuple(
            _zone(snapshot, tuple(history[snapshot.symbol]), policy)
            for snapshot in sorted(group, key=lambda item: item.symbol)
        )
        indices = tuple(
            _index_context(
                index,
                index_ticks[index],
                decision_at,
                policy,
                stale_after_seconds=stale_after_seconds,
            )
            for index in sorted(configuration.market.indices)
        )
        statuses = (
            tuple(
                MarketStatusContext(
                    market=market,
                    status="LO",
                    age_seconds=Decimal(0),
                    is_tradable=True,
                    source_kind="configured_market_calendar",
                    source_record_sha256=sha256(
                        canonical_json(
                            {
                                "context_configuration_sha256": policy.sha256,
                                "decision_at": decision_at.isoformat(),
                                "market": market,
                                "source_kind": "configured_market_calendar",
                            }
                        )
                    ),
                    reasons=(),
                )
                for market in sorted(configuration.market.status_markets)
            )
            if data_mode == "HISTORICAL_PROXY"
            else tuple(
                _market_status_context(market, status_ticks[market], decision_at, policy)
                for market in sorted(configuration.market.status_markets)
            )
        )
        confirmation, regime, reasons = _market_state(indices, statuses, policy)
        contexts.append(
            DecisionContext(
                context_version=policy.version,
                context_configuration_sha256=policy.sha256,
                feature_configuration_sha256=group[0].configuration_sha256,
                data_mode=data_mode,
                trade_date=group[0].trade_date,
                decision_at=decision_at,
                zones=zones,
                indices=indices,
                market_statuses=statuses,
                market_confirmation_strength=confirmation,
                regime=regime,
                reasons=reasons,
            )
        )
        for snapshot in group:
            symbol_history = history[snapshot.symbol]
            symbol_history.append(snapshot)
            cutoff = decision_at - timedelta(seconds=policy.zone_lookback_seconds)
            while symbol_history and symbol_history[0].decision_at < cutoff:
                symbol_history.popleft()
    return tuple(contexts)


def build_decision_contexts(
    snapshots: Sequence[FeatureSnapshot],
    envelopes: Iterable[StreamEnvelope],
    configuration: TradingVersion,
    policy: ContextVersion,
) -> tuple[DecisionContext, ...]:
    """Build live contexts using only index messages received before each clock."""
    timezone = ZoneInfo(configuration.market.timezone)
    observations: list[IndexObservation] = []
    statuses: list[MarketStatusObservation] = []
    for envelope in envelopes:
        if (tick := _index_tick(envelope, set(configuration.market.indices), timezone)) is not None:
            observations.append(tick)
        if (
            status := _market_status_tick(
                envelope,
                set(configuration.market.status_markets),
            )
        ) is not None:
            statuses.append(status)
    return build_decision_contexts_from_observations(
        snapshots,
        observations,
        configuration,
        policy,
        data_mode="LIVE",
        market_status_observations=statuses,
    )


class LiveDecisionContextEngine:
    """Incremental as-of context builder for one live shadow session.

    The engine owns only context state. Feature state remains owned by
    :class:`FeatureEngine`, so capture, features, and context can fail independently.
    """

    def __init__(self, configuration: TradingVersion, policy: ContextVersion) -> None:
        self._configuration = configuration
        self._policy = policy
        self._timezone = ZoneInfo(configuration.market.timezone)
        self._index_ticks: dict[str, list[IndexObservation]] = {
            index: [] for index in configuration.market.indices
        }
        self._status_ticks: dict[str, list[MarketStatusObservation]] = {
            market: [] for market in configuration.market.status_markets
        }
        self._history: dict[str, deque[FeatureSnapshot]] = {
            symbol: deque() for symbol in configuration.market.symbols
        }
        self._last_received_at: datetime | None = None
        self._last_decision_at: datetime | None = None

    def apply(self, envelope: StreamEnvelope) -> None:
        if self._last_received_at is not None and envelope.received_at < self._last_received_at:
            raise ValueError("context envelopes must be receipt ordered")
        self._last_received_at = envelope.received_at
        index = _index_tick(envelope, set(self._index_ticks), self._timezone)
        if index is not None:
            self._index_ticks[index.index].append(index)
        status = _market_status_tick(envelope, set(self._status_ticks))
        if status is not None:
            self._status_ticks[status.market].append(status)

    def build(self, snapshots: Sequence[FeatureSnapshot]) -> DecisionContext:
        if not snapshots:
            raise ValueError("live context requires feature snapshots")
        decision_times = {item.decision_at for item in snapshots}
        if len(decision_times) != 1:
            raise ValueError("live context requires one feature clock")
        decision_at = next(iter(decision_times))
        if self._last_decision_at is not None and decision_at <= self._last_decision_at:
            raise ValueError("live context clocks must be strictly increasing")
        expected_symbols = tuple(sorted(self._configuration.market.symbols))
        ordered = tuple(sorted(snapshots, key=lambda item: item.symbol))
        if (
            tuple(item.symbol for item in ordered) != expected_symbols
            or len({item.trade_date for item in ordered}) != 1
            or len({item.configuration_sha256 for item in ordered}) != 1
        ):
            raise ValueError("live context requires the complete feature matrix")
        if not self._policy.contains(ordered[0].trade_date):
            raise ValueError("live context policy must cover the feature trade date")
        zones = tuple(
            _zone(item, tuple(self._history[item.symbol]), self._policy) for item in ordered
        )
        indices = tuple(
            _index_context(
                index,
                self._index_ticks[index],
                decision_at,
                self._policy,
                stale_after_seconds=self._policy.index_stale_after_seconds,
            )
            for index in sorted(self._index_ticks)
        )
        statuses = tuple(
            _market_status_context(
                market,
                self._status_ticks[market],
                decision_at,
                self._policy,
            )
            for market in sorted(self._status_ticks)
        )
        confirmation, regime, reasons = _market_state(indices, statuses, self._policy)
        context = DecisionContext(
            context_version=self._policy.version,
            context_configuration_sha256=self._policy.sha256,
            feature_configuration_sha256=ordered[0].configuration_sha256,
            data_mode="LIVE",
            trade_date=ordered[0].trade_date,
            decision_at=decision_at,
            zones=zones,
            indices=indices,
            market_statuses=statuses,
            market_confirmation_strength=confirmation,
            regime=regime,
            reasons=reasons,
        )
        cutoff = decision_at - timedelta(seconds=self._policy.zone_lookback_seconds)
        for snapshot in ordered:
            history = self._history[snapshot.symbol]
            history.append(snapshot)
            while history and history[0].decision_at < cutoff:
                history.popleft()
        self._last_decision_at = decision_at
        return context
