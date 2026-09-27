"""Research baseline gates must stay point-in-time and separate from live decisions."""

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from t0_trading.configuration import load_configuration
from t0_trading.controls import CostPolicy
from t0_trading.features import FeatureSnapshot, WindowFeatures
from t0_trading.market.session import MarketSession
from t0_trading.numeric import basis_points
from t0_trading.outcomes import OutcomeLabel
from t0_trading.strategy.baseline_audit import evaluate_buy_first_baselines
from t0_trading.strategy.baseline_walk_forward import evaluate_baseline_walk_forward
from t0_trading.strategy.baselines import BaselineCandidate, score_buy_first_baselines

TRADE_DATE = date(2026, 9, 21)
DECISION_AT = datetime(2026, 9, 21, 2, 30, tzinfo=UTC)
CONFIGURATION = "t0-trading/config/trading.yaml"


def _window(seconds: int, movement: str, flow: str, *, stretch: str) -> WindowFeatures:
    volume = 1000
    imbalance = Decimal(flow)
    return WindowFeatures(
        window_seconds=seconds,
        trade_count=3,
        quote_change_count=1,
        trade_volume=volume,
        signed_trade_volume=int(imbalance * volume),
        trade_volume_per_second=Decimal(volume) / seconds,
        trade_volume_imbalance=imbalance,
        level_one_order_flow_imbalance=100,
        price_return_bps=Decimal(movement),
        realized_volatility_bps=Decimal(20),
        vwap=Decimal(100),
        last_price_to_vwap_bps=Decimal(stretch),
    )


def _snapshot(
    symbol: str,
    decision_at: datetime = DECISION_AT,
    *,
    long_return: str = "30",
    short_return: str = "10",
    stretch: str = "-20",
    reasons: tuple[str, ...] = (),
) -> FeatureSnapshot:
    return FeatureSnapshot(
        feature_version="microstructure-v1",
        configuration_version="market-state-v1",
        configuration_sha256="a" * 64,
        symbol=symbol,
        trade_date=TRADE_DATE,
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
        bid_depth=600,
        ask_depth=400,
        level_one_imbalance=Decimal("0.2"),
        depth_imbalance=Decimal("0.1"),
        windows=(
            _window(30, short_return, "0.6", stretch=stretch),
            _window(60, "20", "0.6", stretch=stretch),
            _window(300, long_return, "0.6", stretch=stretch),
        ),
        reasons=reasons,
    )


def _label(snapshot: FeatureSnapshot, horizon: int = 60) -> OutcomeLabel:
    entry, exit_price = Decimal(100), Decimal("100.2")
    return OutcomeLabel(
        outcome_version="top3-taker-markout-v1",
        outcome_configuration_sha256="b" * 64,
        feature_version=snapshot.feature_version,
        feature_configuration_sha256=snapshot.configuration_sha256,
        feature_snapshot_sha256=snapshot.sha256,
        stream_session_id="session-1",
        symbol=snapshot.symbol,
        trade_date=snapshot.trade_date,
        decision_at=snapshot.decision_at,
        action="BUY",
        horizon_seconds=horizon,
        order_quantity=100,
        entry_at=snapshot.decision_at + timedelta(milliseconds=500),
        horizon_at=snapshot.decision_at + timedelta(seconds=horizon),
        entry_quote_received_at=snapshot.decision_at,
        entry_receive_sequence=10,
        entry_vwap=entry,
        horizon_quote_received_at=snapshot.decision_at + timedelta(seconds=horizon),
        horizon_receive_sequence=11,
        horizon_vwap=exit_price,
        gross_return_bps=basis_points(exit_price - entry, entry),
        reasons=(),
    )


def _costs() -> CostPolicy:
    return CostPolicy(
        version="dta-research-v1",
        effective_from=TRADE_DATE,
        buy_fee_bps=Decimal(10),
        sell_fee_bps=Decimal(10),
        sell_tax_bps=Decimal(10),
        extra_slippage_bps=Decimal(0),
    )


def test_baselines_match_brief_groups_without_emitting_orders() -> None:
    vic = _snapshot("VIC")
    vhm = _snapshot("VHM", long_return="60")
    lagged_vhm = _snapshot("VHM", DECISION_AT - timedelta(seconds=30), long_return="60")

    first = score_buy_first_baselines((vic, lagged_vhm, vhm))
    repeated = score_buy_first_baselines((vhm, vic, lagged_vhm))

    assert first == repeated
    assert len(first) == 9
    assert len({item.sha256 for item in first}) == 9
    selected = {(item.symbol, item.decision_at, item.strategy): item for item in first}
    mean = selected["VIC", DECISION_AT, "mean_reversion"]
    momentum = selected["VIC", DECISION_AT, "momentum_pullback"]
    relative = selected["VIC", DECISION_AT, "vic_vhm_relative"]
    assert not mean.is_candidate
    assert type(mean).model_validate_json(mean.model_dump_json()) == mean
    assert [group.status for group in mean.groups] == ["MATCH", "UNAVAILABLE", "MATCH"]
    assert mean.block_reasons == ("required_group_unavailable",)
    assert momentum.is_candidate
    assert [group.status for group in momentum.groups] == ["MATCH", "FAIL", "MATCH"]
    assert not relative.is_candidate
    assert [group.status for group in relative.groups] == ["MATCH", "MATCH", "UNAVAILABLE"]
    assert relative.block_reasons == ("required_group_unavailable",)
    assert relative.peer_feature_snapshot_sha256 == vhm.sha256


def test_relative_baseline_cannot_use_future_or_missing_peer_clock() -> None:
    vic = _snapshot("VIC")
    vhm = _snapshot("VHM", long_return="60")
    without_prior = score_buy_first_baselines((vic, vhm))
    future_peer = _snapshot("VHM", DECISION_AT + timedelta(seconds=30), long_return="60")
    with_future = score_buy_first_baselines((vic, vhm, future_peer))

    def find(rows: Sequence[BaselineCandidate]) -> BaselineCandidate:
        return next(
            item
            for item in rows
            if item.symbol == "VIC"
            and item.decision_at == DECISION_AT
            and item.strategy == "vic_vhm_relative"
        )

    assert find(without_prior) == find(with_future)
    assert find(without_prior).block_reasons == ("required_group_unavailable",)
    assert find(without_prior).groups[1].status == "UNAVAILABLE"

    missing_peer = next(
        item for item in score_buy_first_baselines((vic,)) if item.strategy == "vic_vhm_relative"
    )
    assert missing_peer.block_reasons == ("missing_peer",)


def test_ineligible_feature_abstains_for_all_baselines() -> None:
    candidates = score_buy_first_baselines((_snapshot("VIC", reasons=("stale_quote",)),))
    assert len(candidates) == 3
    assert all(item.block_reasons == ("feature_ineligible",) for item in candidates)
    assert all(not item.is_candidate for item in candidates)

    auction = _snapshot("VIC").model_copy(update={"market_session": MarketSession.OPENING_AUCTION})
    auction_candidates = score_buy_first_baselines((auction,))
    assert all(item.block_reasons == ("not_continuous_session",) for item in auction_candidates)


def test_baseline_input_quality_fails_closed() -> None:
    snapshot = _snapshot("VIC")
    with pytest.raises(ValueError, match="must be unique"):
        score_buy_first_baselines((snapshot, snapshot))

    missing_window = snapshot.model_copy(update={"windows": snapshot.windows[:-1]})
    with pytest.raises(ValueError, match="300-second"):
        score_buy_first_baselines((missing_window,))


def test_baseline_audit_preserves_missing_coverage_and_conditional_net_loss() -> None:
    vic = _snapshot("VIC")
    vic_again = _snapshot("VIC", DECISION_AT + timedelta(seconds=5))
    vhm = _snapshot("VHM", long_return="60")
    lagged_vhm = _snapshot("VHM", DECISION_AT - timedelta(seconds=30), long_return="60")
    candidates = score_buy_first_baselines((vic, vic_again, vhm, lagged_vhm))
    labels = tuple(_label(snapshot) for snapshot in (vic, vic_again, vhm, lagged_vhm))

    report = evaluate_buy_first_baselines(candidates, labels, _costs())
    repeated = evaluate_buy_first_baselines(candidates, labels, _costs())
    assert report.sha256 == repeated.sha256
    assert type(report).model_validate_json(report.model_dump_json()) == report
    assert len(report.evaluations) == 6
    assert report.nonoverlap_seconds == 60
    assert report.context_clock_count == 3
    assert report.context_regime_counts == {"UNKNOWN": 3}
    vic_momentum = next(
        item
        for item in report.evaluations
        if item.symbol == "VIC" and item.strategy == "momentum_pullback"
    )
    assert vic_momentum.raw_candidate_count == 2
    assert vic_momentum.candidate_count == 1
    assert vic_momentum.eligible_outcome_count == 1
    assert vic_momentum.average_gross_return_bps == Decimal("20.0000")
    net_return = vic_momentum.average_conditional_net_return_bps
    assert net_return is not None
    assert net_return < 0
    assert vic_momentum.positive_gross_count == 1
    assert vic_momentum.positive_net_count == 0
    zone = next(
        item
        for item in report.group_coverage
        if item.symbol == "VIC" and item.strategy == "mean_reversion" and item.group == "zone"
    )
    assert zone.observed_count == 2
    assert zone.available_count == 0

    with pytest.raises(ValueError, match="complete strategy matrix"):
        evaluate_buy_first_baselines(candidates[:-1], labels, _costs())

    with pytest.raises(ValueError, match="cover every candidate horizon"):
        evaluate_buy_first_baselines(candidates, labels[:-1], _costs())


def test_baseline_walk_forward_uses_its_own_purged_exploratory_policy() -> None:
    vic = _snapshot("VIC")
    vic_again = _snapshot("VIC", DECISION_AT + timedelta(seconds=5))
    vhm = _snapshot("VHM", long_return="60")
    lagged_vhm = _snapshot("VHM", DECISION_AT - timedelta(seconds=30), long_return="60")
    daily = evaluate_buy_first_baselines(
        score_buy_first_baselines((vic, vic_again, vhm, lagged_vhm)),
        tuple(_label(snapshot) for snapshot in (vic, vic_again, vhm, lagged_vhm)),
        _costs(),
    ).model_copy(
        update={
            "context_version": "decision-context-v3",
            "context_configuration_sha256": "c" * 64,
            "context_data_mode": "HISTORICAL_PROXY",
        }
    )
    dates = tuple(TRADE_DATE + timedelta(days=offset) for offset in range(16))
    sessions = {
        trade_date: daily.model_copy(update={"trade_date": trade_date}) for trade_date in dates
    }
    configuration = load_configuration(Path(CONFIGURATION))
    policy = configuration.resolve_baseline_evaluation(dates[0], "EXPLORATORY")

    report = evaluate_baseline_walk_forward(sessions, policy)

    assert report.evaluation_tier == "EXPLORATORY"
    assert report.promotion_eligible is False
    assert len(report.folds) == 1
    assert report.folds[0].development_dates == dates[:10]
    assert report.folds[0].purged_dates == dates[10:11]
    assert report.folds[0].holdout_dates == dates[11:]
    assert report.pending_dates == ()
    with pytest.raises(ValueError, match="at least 16"):
        evaluate_baseline_walk_forward(dict(tuple(sessions.items())[:-1]), policy)
