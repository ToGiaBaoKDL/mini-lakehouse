from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from t0_trading.configuration import load_configuration
from t0_trading.features import FeatureSnapshot, WindowFeatures
from t0_trading.market.session import MarketSession
from t0_trading.numeric import basis_points
from t0_trading.outcomes import OutcomeLabel
from t0_trading.strategy import evaluate_scores, evaluate_walk_forward, score_features

CONFIGURATION = Path("t0-trading/config/trading.yaml")
TRADE_DATE = date(2026, 9, 4)
DECISION_AT = datetime(2026, 9, 4, 2, 30, tzinfo=UTC)


def _policies():
    configuration = load_configuration(CONFIGURATION)
    return (
        configuration.resolve(TRADE_DATE),
        configuration.resolve_outcomes(TRADE_DATE),
        configuration.resolve_strategies(TRADE_DATE),
    )


def _window(
    seconds: int,
    *,
    price_return_bps: str,
    realized_volatility_bps: str,
    trade_volume_imbalance: str,
    book_flow: int,
) -> WindowFeatures:
    imbalance = Decimal(trade_volume_imbalance)
    return WindowFeatures(
        window_seconds=seconds,
        trade_count=2,
        quote_change_count=1,
        trade_volume=100,
        signed_trade_volume=int(imbalance * 100),
        trade_volume_per_second=Decimal(100) / seconds,
        trade_volume_imbalance=imbalance,
        level_one_order_flow_imbalance=book_flow,
        price_return_bps=Decimal(price_return_bps),
        realized_volatility_bps=Decimal(realized_volatility_bps),
        vwap=Decimal(100),
        last_price_to_vwap_bps=Decimal(price_return_bps),
    )


def _snapshot(
    symbol: str,
    *,
    trade_date: date = TRADE_DATE,
) -> FeatureSnapshot:
    configuration, _, _ = _policies()
    decision_at = datetime(trade_date.year, trade_date.month, trade_date.day, 2, 30, tzinfo=UTC)
    is_vic = symbol == "VIC"
    if is_vic:
        windows = (
            _window(
                30,
                price_return_bps="10",
                realized_volatility_bps="20",
                trade_volume_imbalance="0.5",
                book_flow=100,
            ),
            _window(
                60,
                price_return_bps="20",
                realized_volatility_bps="40",
                trade_volume_imbalance="0.5",
                book_flow=100,
            ),
            _window(
                300,
                price_return_bps="30",
                realized_volatility_bps="60",
                trade_volume_imbalance="0.5",
                book_flow=100,
            ),
        )
    else:
        windows = (
            _window(
                30,
                price_return_bps="-5",
                realized_volatility_bps="10",
                trade_volume_imbalance="-0.25",
                book_flow=-100,
            ),
            _window(
                60,
                price_return_bps="-10",
                realized_volatility_bps="20",
                trade_volume_imbalance="-0.25",
                book_flow=-100,
            ),
            _window(
                300,
                price_return_bps="10",
                realized_volatility_bps="40",
                trade_volume_imbalance="-0.25",
                book_flow=-100,
            ),
        )
    return FeatureSnapshot(
        feature_version=configuration.features.version,
        configuration_version=configuration.version,
        configuration_sha256=configuration.sha256,
        symbol=symbol,
        trade_date=trade_date,
        decision_at=decision_at,
        market_session=MarketSession.CONTINUOUS_AM,
        stream_session_id="session-1",
        last_receive_sequence=10,
        trade_age_seconds=Decimal(1),
        quote_age_seconds=Decimal(1),
        mid_price=Decimal(100),
        microprice=Decimal(100),
        microprice_deviation_bps=Decimal(0),
        spread=Decimal(1),
        spread_bps=Decimal(100),
        bid_depth=600 if is_vic else 400,
        ask_depth=400 if is_vic else 600,
        level_one_imbalance=Decimal("0.2") if is_vic else Decimal("-0.2"),
        depth_imbalance=Decimal("0.1") if is_vic else Decimal("-0.1"),
        windows=windows,
        reasons=(),
    )


def _labels(snapshots: tuple[FeatureSnapshot, ...]) -> tuple[OutcomeLabel, ...]:
    _, outcome_policy, _ = _policies()
    labels = []
    for snapshot in snapshots:
        for action in ("BUY", "SELL"):
            for horizon in outcome_policy.horizons_seconds:
                entry = Decimal(100)
                exit_price = Decimal(101) if action == "BUY" else Decimal(99)
                movement = exit_price - entry if action == "BUY" else entry - exit_price
                labels.append(
                    OutcomeLabel(
                        outcome_version=outcome_policy.version,
                        outcome_configuration_sha256=outcome_policy.sha256,
                        feature_version=snapshot.feature_version,
                        feature_configuration_sha256=snapshot.configuration_sha256,
                        feature_snapshot_sha256=snapshot.sha256,
                        stream_session_id="session-1",
                        symbol=snapshot.symbol,
                        trade_date=snapshot.trade_date,
                        decision_at=snapshot.decision_at,
                        action=action,
                        horizon_seconds=horizon,
                        order_quantity=outcome_policy.order_quantity,
                        entry_at=snapshot.decision_at + timedelta(milliseconds=500),
                        horizon_at=snapshot.decision_at + timedelta(seconds=horizon),
                        entry_quote_received_at=snapshot.decision_at,
                        entry_receive_sequence=10,
                        entry_vwap=entry,
                        horizon_quote_received_at=snapshot.decision_at + timedelta(seconds=horizon),
                        horizon_receive_sequence=11,
                        horizon_vwap=exit_price,
                        gross_return_bps=basis_points(movement, entry),
                        reasons=(),
                    )
                )
    return tuple(labels)


def test_strategy_scores_are_exact_versioned_and_input_order_independent() -> None:
    configuration, _, strategy_policy = _policies()
    vic, vhm = _snapshot("VIC"), _snapshot("VHM")

    scores = score_features((vhm, vic), configuration, strategy_policy)
    repeated = score_features((vic, vhm), configuration, strategy_policy)

    assert scores == repeated
    assert len(scores) == 6
    assert len({score.sha256 for score in scores}) == 6
    values = {(score.symbol, score.strategy): score for score in scores}
    assert values["VIC", "momentum"].signed_score == Decimal("0.25000000")
    assert values["VIC", "momentum"].direction == "BUY"
    assert values["VHM", "momentum"].signed_score == Decimal("-0.12500000")
    assert values["VHM", "momentum"].direction == "SELL"
    assert values["VIC", "order_flow"].signed_score == Decimal("0.22500000")
    assert values["VHM", "order_flow"].signed_score == Decimal("-0.16250000")
    assert values["VIC", "relative_value"].signed_score == Decimal("-0.20000000")
    assert values["VHM", "relative_value"].signed_score == Decimal("0.20000000")
    assert values["VIC", "relative_value"].peer_feature_snapshot_sha256 == vhm.sha256


def test_unconfirmed_momentum_abstains_without_a_threshold() -> None:
    configuration, _, strategy_policy = _policies()
    snapshot = _snapshot("VIC")
    windows = tuple(
        window.model_copy(update={"trade_volume_imbalance": Decimal("-0.5")})
        if window.window_seconds == 60
        else window
        for window in snapshot.windows
    )
    snapshot = snapshot.model_copy(update={"windows": windows})

    score = next(
        item
        for item in score_features((snapshot,), configuration, strategy_policy)
        if item.strategy == "momentum"
    )

    assert score.signed_score == 0
    assert score.direction is None

    ineligible = snapshot.model_copy(update={"reasons": ("stale_quote",)})
    assert score_features((ineligible,), configuration, strategy_policy) == ()


def test_strategy_evaluation_matches_exact_outcomes_and_reports_gross_coverage() -> None:
    configuration, outcome_policy, strategy_policy = _policies()
    snapshots = (_snapshot("VIC"), _snapshot("VHM"))
    scores = score_features(snapshots, configuration, strategy_policy)
    labels = _labels(snapshots)

    report = evaluate_scores(
        scores,
        labels,
        strategy_policy,
        outcome_policy,
        trade_date=TRADE_DATE,
    )
    repeated = evaluate_scores(
        scores,
        labels,
        strategy_policy,
        outcome_policy,
        trade_date=TRADE_DATE,
    )

    assert report.model_dump_json() == repeated.model_dump_json()
    assert report.score_count == 6
    assert report.directed_score_count == 6
    assert len(report.evaluations) == 9
    assert all(item.score_count == 2 for item in report.evaluations)
    assert all(item.directed_rate == Decimal("1.000000") for item in report.evaluations)
    assert all(item.outcome_coverage_rate == Decimal("1.000000") for item in report.evaluations)
    assert all(item.positive_outcome_rate == Decimal("1.000000") for item in report.evaluations)
    assert all(item.average_gross_return_bps == Decimal("100.0000") for item in report.evaluations)

    with pytest.raises(ValueError, match="cover every directed"):
        evaluate_scores(
            scores,
            labels[:-1],
            strategy_policy,
            outcome_policy,
            trade_date=TRADE_DATE,
        )


def test_strategy_evaluation_fails_closed_on_duplicate_or_wrong_policy_lineage() -> None:
    configuration, outcome_policy, strategy_policy = _policies()
    snapshots = (_snapshot("VIC"), _snapshot("VHM"))
    scores = score_features(snapshots, configuration, strategy_policy)
    labels = _labels(snapshots)

    with pytest.raises(ValueError, match="scores must be unique"):
        evaluate_scores(
            (*scores, scores[0]),
            labels,
            strategy_policy,
            outcome_policy,
            trade_date=TRADE_DATE,
        )

    wrong_lineage = labels[0].model_copy(update={"outcome_configuration_sha256": "0" * 64})
    with pytest.raises(ValueError, match="policy lineage"):
        evaluate_scores(
            scores,
            (wrong_lineage, *labels[1:]),
            strategy_policy,
            outcome_policy,
            trade_date=TRADE_DATE,
        )


def test_walk_forward_uses_training_quantiles_and_explicit_purge_sessions() -> None:
    configuration = load_configuration(CONFIGURATION)
    strategy_policy = configuration.resolve_strategies(TRADE_DATE)
    outcome_policy = configuration.resolve_outcomes(TRADE_DATE)
    evaluation_policy = configuration.resolve_strategy_evaluation(TRADE_DATE).model_copy(
        update={
            "score_bucket_count": 2,
            "minimum_training_sessions": 2,
            "validation_sessions": 1,
            "purge_sessions": 1,
        }
    )
    dates = tuple(date(2026, 9, day) for day in range(1, 7))
    sessions = {}
    for index, trade_date in enumerate(dates, start=1):
        snapshots = (
            _snapshot("VIC", trade_date=trade_date),
            _snapshot("VHM", trade_date=trade_date),
        )
        scores = score_features(snapshots, configuration.resolve(trade_date), strategy_policy)
        multiplier = Decimal(index) / 10
        sessions[trade_date] = (
            tuple(
                score.model_copy(update={"signed_score": score.signed_score * multiplier})
                for score in scores
            ),
            _labels(snapshots),
        )

    report = evaluate_walk_forward(
        sessions,
        strategy_policy,
        outcome_policy,
        evaluation_policy,
    )
    repeated = evaluate_walk_forward(
        dict(reversed(tuple(sessions.items()))),
        strategy_policy,
        outcome_policy,
        evaluation_policy,
    )

    assert report.sha256 == repeated.sha256
    assert len(report.folds) == 3
    assert report.pending_dates == ()
    first = report.folds[0]
    assert first.training_dates == dates[:2]
    assert first.purged_dates == (dates[2],)
    assert first.validation_dates == (dates[3],)
    assert len(first.evaluations) == 36
    first_momentum_buy_bucket = next(
        item
        for item in first.evaluations
        if item.strategy == "momentum"
        and item.direction == "BUY"
        and item.horizon_seconds == 30
        and item.bucket == 1
    )
    assert first_momentum_buy_bucket.upper_strength_inclusive == Decimal("0.025")
    assert all(item.score_count == 0 for item in first.evaluations if item.bucket == 1)
    assert all(
        item.score_count == 1
        and item.eligible_outcome_count == 1
        and item.positive_outcome_rate == Decimal("1.000000")
        and item.average_gross_return_bps == Decimal("100.0000")
        for item in first.evaluations
        if item.bucket == 2
    )

    with pytest.raises(ValueError, match="requires at least 4 sessions"):
        evaluate_walk_forward(
            {trade_date: sessions[trade_date] for trade_date in dates[:3]},
            strategy_policy,
            outcome_policy,
            evaluation_policy,
        )
