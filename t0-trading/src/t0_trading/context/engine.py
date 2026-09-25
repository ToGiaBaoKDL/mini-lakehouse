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
from ssi_sdk.models import TradeMessage

from t0_trading.configuration import ContextVersion, TradingVersion
from t0_trading.context.model import (
    DecisionContext,
    IndexContext,
    MarketRegime,
    MarketWindowContext,
    ZoneContext,
)
from t0_trading.features import FeatureSnapshot
from t0_trading.identity import canonical_json, sha256
from t0_trading.market.events import StreamEnvelope, provider_timestamp
from t0_trading.numeric import RATIO_QUANTUM, basis_points, ratio

_TRADE_ADAPTER = TypeAdapter(TradeMessage)
_ZERO = Decimal(0)
_ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class _IndexTick:
    index: str
    value: Decimal
    event_time: datetime
    received_at: datetime
    stream_session_id: str
    receive_sequence: int


def _index_tick(
    envelope: StreamEnvelope, indices: set[str], timezone: ZoneInfo
) -> _IndexTick | None:
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
    return _IndexTick(
        index=envelope.symbol,
        value=value,
        event_time=event_time,
        received_at=envelope.received_at,
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


def _as_of(ticks: Sequence[_IndexTick], at: datetime) -> _IndexTick | None:
    return next((tick for tick in reversed(ticks) if tick.received_at <= at), None)


def _window(
    ticks: Sequence[_IndexTick],
    current: _IndexTick,
    decision_at: datetime,
    seconds: int,
) -> MarketWindowContext | None:
    cutoff = decision_at - timedelta(seconds=seconds)
    reference = _as_of(ticks, cutoff)
    if reference is None:
        return None
    observed = [reference]
    observed.extend(tick for tick in ticks if cutoff < tick.received_at <= current.received_at)
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
    ticks: Sequence[_IndexTick],
    decision_at: datetime,
    policy: ContextVersion,
) -> IndexContext:
    current = _as_of(ticks, decision_at)
    if current is None:
        return IndexContext(index=index, windows=(), reasons=("MISSING_INDEX",))
    age = Decimal(str((decision_at - current.received_at).total_seconds()))
    windows = tuple(
        window
        for seconds in policy.market_windows_seconds
        if (window := _window(ticks, current, decision_at, seconds)) is not None
    )
    reasons: list[str] = []
    if age > policy.index_stale_after_seconds:
        reasons.append("STALE_INDEX")
    if tuple(window.window_seconds for window in windows) != policy.market_windows_seconds:
        reasons.append("INSUFFICIENT_INDEX_HISTORY")
    return IndexContext(
        index=index,
        value=current.value,
        age_seconds=age,
        stream_session_id=current.stream_session_id,
        receive_sequence=current.receive_sequence,
        windows=windows,
        reasons=tuple(reasons),
    )


def _market_state(
    indices: Sequence[IndexContext], policy: ContextVersion
) -> tuple[Decimal | None, MarketRegime, tuple[str, ...]]:
    reasons = tuple(
        dict.fromkeys(f"{item.index}_{reason}" for item in indices for reason in item.reasons)
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


def build_decision_contexts(
    snapshots: Sequence[FeatureSnapshot],
    envelopes: Iterable[StreamEnvelope],
    configuration: TradingVersion,
    policy: ContextVersion,
) -> tuple[DecisionContext, ...]:
    """Build one context per feature clock using only already-received evidence."""
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

    index_ticks: dict[str, list[_IndexTick]] = {index: [] for index in configuration.market.indices}
    last_received_at: datetime | None = None
    for envelope in envelopes:
        if last_received_at is not None and envelope.received_at < last_received_at:
            raise ValueError("context envelopes must be receipt ordered")
        last_received_at = envelope.received_at
        tick = _index_tick(
            envelope,
            set(configuration.market.indices),
            ZoneInfo(configuration.market.timezone),
        )
        if tick is not None:
            index_ticks[tick.index].append(tick)

    history: dict[str, deque[FeatureSnapshot]] = {
        symbol: deque() for symbol in configuration.market.symbols
    }
    contexts: list[DecisionContext] = []
    for decision_at, group in sorted(clocks.items()):
        zones = tuple(
            _zone(snapshot, tuple(history[snapshot.symbol]), policy)
            for snapshot in sorted(group, key=lambda item: item.symbol)
        )
        indices = tuple(
            _index_context(index, index_ticks[index], decision_at, policy)
            for index in sorted(configuration.market.indices)
        )
        confirmation, regime, reasons = _market_state(indices, policy)
        contexts.append(
            DecisionContext(
                context_version=policy.version,
                context_configuration_sha256=policy.sha256,
                feature_configuration_sha256=group[0].configuration_sha256,
                trade_date=group[0].trade_date,
                decision_at=decision_at,
                zones=zones,
                indices=indices,
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
