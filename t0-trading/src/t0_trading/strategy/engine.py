"""Deterministic strategy scores over existing point-in-time features."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from t0_trading.configuration import StrategyVersion, TradingVersion
from t0_trading.features import FeatureSnapshot, WindowFeatures
from t0_trading.numeric import RATIO_QUANTUM, ratio
from t0_trading.strategy.model import StrategyName, StrategyScore

_ZERO = Decimal(0)
_ONE = Decimal(1)


def _clamp(value: Decimal) -> Decimal:
    return ratio(max(-_ONE, min(_ONE, value)), 1, quantum=RATIO_QUANTUM)


def _normalized(numerator: Decimal | int, denominator: Decimal | int) -> Decimal:
    if denominator == 0:
        return _ZERO
    return _clamp(ratio(numerator, denominator))


def _window(snapshot: FeatureSnapshot, seconds: int) -> WindowFeatures:
    matches = tuple(window for window in snapshot.windows if window.window_seconds == seconds)
    if len(matches) != 1:
        raise ValueError(f"feature snapshot must contain one {seconds}-second window")
    return matches[0]


def _momentum(snapshot: FeatureSnapshot, seconds: int) -> Decimal:
    window = _window(snapshot, seconds)
    movement = window.price_return_bps
    volatility = window.realized_volatility_bps
    flow = window.trade_volume_imbalance
    if movement is None or volatility is None or flow is None:
        raise ValueError("eligible feature snapshot is missing momentum inputs")
    if movement == 0 or flow == 0 or movement * flow <= 0:
        return _ZERO
    magnitude = _normalized(abs(movement), volatility) * abs(flow)
    return _clamp(magnitude if movement > 0 else -magnitude)


def _order_flow(snapshot: FeatureSnapshot, seconds: int) -> Decimal:
    window = _window(snapshot, seconds)
    if (
        snapshot.level_one_imbalance is None
        or snapshot.depth_imbalance is None
        or snapshot.bid_depth is None
        or snapshot.ask_depth is None
        or window.trade_volume_imbalance is None
    ):
        raise ValueError("eligible feature snapshot is missing order-flow inputs")
    normalized_book_flow = _normalized(
        window.level_one_order_flow_imbalance,
        snapshot.bid_depth + snapshot.ask_depth,
    )
    return _clamp(
        (
            snapshot.level_one_imbalance
            + snapshot.depth_imbalance
            + window.trade_volume_imbalance
            + normalized_book_flow
        )
        / 4
    )


def _relative_value(
    first: FeatureSnapshot,
    second: FeatureSnapshot,
    seconds: int,
) -> Decimal:
    first_window = _window(first, seconds)
    second_window = _window(second, seconds)
    first_return = first_window.price_return_bps
    first_volatility = first_window.realized_volatility_bps
    second_return = second_window.price_return_bps
    second_volatility = second_window.realized_volatility_bps
    if (
        first_return is None
        or first_volatility is None
        or second_return is None
        or second_volatility is None
    ):
        raise ValueError("eligible feature pair is missing relative-value inputs")
    # Mean-reversion orientation: sell the relative outperformer and buy the underperformer.
    return -_normalized(
        first_return - second_return,
        first_volatility + second_volatility,
    )


def _score(
    snapshot: FeatureSnapshot,
    policy: StrategyVersion,
    strategy: StrategyName,
    signed_score: Decimal,
    *,
    peer: FeatureSnapshot | None = None,
) -> StrategyScore:
    return StrategyScore(
        strategy=strategy,
        strategy_version=policy.version,
        strategy_configuration_sha256=policy.sha256,
        feature_version=snapshot.feature_version,
        feature_configuration_sha256=snapshot.configuration_sha256,
        feature_snapshot_sha256=snapshot.sha256,
        peer_feature_snapshot_sha256=peer.sha256 if peer is not None else None,
        symbol=snapshot.symbol,
        trade_date=snapshot.trade_date,
        decision_at=snapshot.decision_at,
        signed_score=signed_score,
    )


def _validate_inputs(
    snapshots: Sequence[FeatureSnapshot],
    configuration: TradingVersion,
    policy: StrategyVersion,
) -> date:
    if not snapshots:
        raise ValueError("strategy scoring requires feature snapshots")
    trade_dates = {snapshot.trade_date for snapshot in snapshots}
    if len(trade_dates) != 1:
        raise ValueError("strategy scoring requires exactly one trade date")
    trade_date = next(iter(trade_dates))
    if not configuration.contains(trade_date) or not policy.contains(trade_date):
        raise ValueError("strategy policy is not effective for the feature trade date")
    required_windows = {
        policy.momentum_window_seconds,
        policy.order_flow_window_seconds,
        policy.relative_value_window_seconds,
    }
    if not required_windows.issubset(configuration.features.windows_seconds):
        raise ValueError("strategy windows must exist in the effective feature configuration")
    if not set(policy.relative_value_symbols).issubset(configuration.market.symbols):
        raise ValueError("relative-value symbols must exist in the effective market configuration")
    identities = {snapshot.sha256 for snapshot in snapshots}
    keys = {(snapshot.symbol, snapshot.decision_at) for snapshot in snapshots}
    if len(identities) != len(snapshots) or len(keys) != len(snapshots):
        raise ValueError("strategy feature snapshots must be unique")
    if any(
        snapshot.feature_version != configuration.features.version
        or snapshot.configuration_version != configuration.version
        or snapshot.configuration_sha256 != configuration.sha256
        or snapshot.symbol not in configuration.market.symbols
        for snapshot in snapshots
    ):
        raise ValueError("strategy feature lineage is inconsistent")
    return trade_date


def score_features(
    snapshots: Sequence[FeatureSnapshot],
    configuration: TradingVersion,
    policy: StrategyVersion,
) -> tuple[StrategyScore, ...]:
    """Generate threshold-free scores; zero means abstain, not a weak signal."""
    _validate_inputs(snapshots, configuration, policy)
    eligible = tuple(snapshot for snapshot in snapshots if snapshot.is_eligible)
    scores = [
        score
        for snapshot in eligible
        for score in (
            _score(
                snapshot,
                policy,
                "momentum",
                _momentum(snapshot, policy.momentum_window_seconds),
            ),
            _score(
                snapshot,
                policy,
                "order_flow",
                _order_flow(snapshot, policy.order_flow_window_seconds),
            ),
        )
    ]

    first_symbol, second_symbol = policy.relative_value_symbols
    by_decision: dict[datetime, dict[str, FeatureSnapshot]] = {}
    for snapshot in eligible:
        by_decision.setdefault(snapshot.decision_at, {})[snapshot.symbol] = snapshot
    for decision_at in sorted(by_decision):
        group = by_decision[decision_at]
        if first_symbol not in group or second_symbol not in group:
            continue
        first, second = group[first_symbol], group[second_symbol]
        first_score = _relative_value(first, second, policy.relative_value_window_seconds)
        scores.extend(
            (
                _score(first, policy, "relative_value", first_score, peer=second),
                _score(second, policy, "relative_value", -first_score, peer=first),
            )
        )

    return tuple(sorted(scores, key=lambda item: (item.decision_at, item.symbol, item.strategy)))
