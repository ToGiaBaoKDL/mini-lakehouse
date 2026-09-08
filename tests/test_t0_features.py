import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from t0_trading.configuration import load_configuration
from t0_trading.features import (
    FeatureEngine,
    build_feature_audit,
    decision_times,
    replay_features,
)
from t0_trading.market import StreamEnvelope

CONFIGURATION = Path("t0-trading/config/trading.yaml")
TRADE_DATE = date(2026, 9, 4)


def _configuration():
    return load_configuration(CONFIGURATION).resolve(TRADE_DATE)


def _received(hour: int, minute: int, second: int) -> datetime:
    return datetime(2026, 9, 4, hour - 7, minute, second, tzinfo=UTC)


def _envelope(
    sequence: int,
    message_type: str,
    payload: dict[str, object],
    *,
    received_at: datetime,
) -> StreamEnvelope:
    message_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return StreamEnvelope(
        stream_session_id="session-1",
        receive_sequence=sequence,
        message_type=message_type,
        symbol="VIC",
        source_time_text=str(payload["trading_time"]),
        received_at=received_at,
        message_json=message_json,
        message_sha256=hashlib.sha256(message_json.encode()).hexdigest(),
    )


def _trade(
    sequence: int,
    *,
    hour: int,
    minute: int,
    second: int,
    price: int,
    quantity: int,
    side: str,
    total_volume: int,
) -> StreamEnvelope:
    return _envelope(
        sequence,
        "TradeMessage",
        {
            "type": "trade",
            "symbol": "VIC",
            "trading_time": f"2026/09/04 {hour:02}:{minute:02}:{second:02}",
            "price": price,
            "quantity": quantity,
            "side": side,
            "total_volume": total_volume,
        },
        received_at=_received(hour, minute, second),
    )


def _quote(
    sequence: int,
    *,
    hour: int,
    minute: int,
    second: int,
    bid: int,
    bid_quantity: int,
    ask: int,
    ask_quantity: int,
) -> StreamEnvelope:
    return _envelope(
        sequence,
        "QuoteMessage",
        {
            "type": "quote",
            "symbol": "VIC",
            "trading_time": f"2026/09/04 {hour:02}:{minute:02}:{second:02}",
            "bid_prices": [bid, bid - 1, bid - 2, *([0] * 7)],
            "bid_volumes": [bid_quantity, 80, 60, *([0] * 7)],
            "ask_prices": [ask, ask + 1, ask + 2, *([0] * 7)],
            "ask_volumes": [ask_quantity, 70, 50, *([0] * 7)],
        },
        received_at=_received(hour, minute, second),
    )


def _observations() -> tuple[StreamEnvelope, ...]:
    return (
        _quote(
            1,
            hour=9,
            minute=15,
            second=1,
            bid=100,
            bid_quantity=100,
            ask=102,
            ask_quantity=90,
        ),
        _trade(
            2,
            hour=9,
            minute=15,
            second=2,
            price=101,
            quantity=10,
            side="B",
            total_volume=10,
        ),
        _quote(
            3,
            hour=9,
            minute=15,
            second=6,
            bid=100,
            bid_quantity=110,
            ask=102,
            ask_quantity=80,
        ),
        _trade(
            4,
            hour=9,
            minute=15,
            second=10,
            price=101,
            quantity=20,
            side="B",
            total_volume=30,
        ),
        _quote(
            5,
            hour=9,
            minute=19,
            second=40,
            bid=101,
            bid_quantity=120,
            ask=103,
            ask_quantity=100,
        ),
        _trade(
            6,
            hour=9,
            minute=19,
            second=45,
            price=102,
            quantity=15,
            side="S",
            total_volume=45,
        ),
        _quote(
            7,
            hour=9,
            minute=19,
            second=50,
            bid=101,
            bid_quantity=130,
            ask=103,
            ask_quantity=80,
        ),
        _trade(
            8,
            hour=9,
            minute=19,
            second=55,
            price=103,
            quantity=25,
            side="B",
            total_volume=70,
        ),
        _quote(
            9,
            hour=9,
            minute=20,
            second=0,
            bid=102,
            bid_quantity=140,
            ask=104,
            ask_quantity=90,
        ),
    )


def _snapshot_at(envelopes: tuple[StreamEnvelope, ...], decision_at: datetime):
    snapshots = replay_features(envelopes, _configuration(), trade_date=TRADE_DATE)
    return next(
        snapshot
        for snapshot in snapshots
        if snapshot.symbol == "VIC" and snapshot.decision_at == decision_at
    )


def test_feature_engine_fails_closed_during_warmup() -> None:
    engine = FeatureEngine(_configuration())
    for envelope in _observations()[:3]:
        engine.apply(envelope)

    snapshot = engine.snapshots(_received(9, 15, 10))[0]

    assert snapshot.symbol == "VIC"
    assert snapshot.is_eligible is False
    assert snapshot.reasons == (
        "WARMUP",
        "INSUFFICIENT_TRADES_30S",
        "INSUFFICIENT_TRADES_60S",
        "INSUFFICIENT_TRADES_300S",
    )


def test_decision_clock_uses_only_configured_continuous_sessions() -> None:
    times = tuple(decision_times(_configuration(), TRADE_DATE))

    assert len(times) == 2_698
    assert times[0] == _received(9, 15, 5)
    assert times[1_618] == _received(11, 29, 55)
    assert times[1_619] == _received(13, 0, 5)
    assert times[-1] == _received(14, 29, 55)


def test_feature_snapshot_has_exact_book_flow_momentum_and_liquidity_values() -> None:
    decision_at = _received(9, 20, 5)
    snapshot = _snapshot_at(_observations(), decision_at)

    assert snapshot.is_eligible is True
    assert snapshot.reasons == ()
    assert snapshot.trade_age_seconds == Decimal(10)
    assert snapshot.quote_age_seconds == Decimal(5)
    assert snapshot.mid_price == Decimal("103.00000000")
    assert snapshot.microprice == Decimal("103.21739130")
    assert snapshot.microprice_deviation_bps == Decimal("21.1060")
    assert snapshot.spread == Decimal(2)
    assert snapshot.spread_bps == Decimal("194.1748")
    assert snapshot.bid_depth == 280
    assert snapshot.ask_depth == 210
    assert snapshot.level_one_imbalance == Decimal("0.21739130")
    assert snapshot.depth_imbalance == Decimal("0.14285714")

    window = snapshot.windows[0]
    assert window.window_seconds == 30
    assert window.trade_count == 2
    assert window.quote_change_count == 3
    assert window.trade_volume == 40
    assert window.signed_trade_volume == 10
    assert window.trade_volume_per_second == Decimal("1.33333333")
    assert window.trade_volume_imbalance == Decimal("0.25000000")
    assert window.level_one_order_flow_imbalance == 450
    assert window.price_return_bps == Decimal("98.0392")
    assert window.realized_volatility_bps == Decimal("98.0392")
    assert window.vwap == Decimal("102.62500000")
    assert window.last_price_to_vwap_bps == Decimal("36.5408")


def test_live_clock_and_full_replay_emit_identical_point_in_time_snapshots() -> None:
    configuration = _configuration()
    envelopes = _observations()
    decision_at = _received(9, 20, 5)
    live = FeatureEngine(configuration)
    live_snapshots = []
    pending = iter(envelopes)
    envelope = next(pending, None)
    for current in decision_times(configuration, TRADE_DATE):
        if current > decision_at:
            break
        while envelope is not None and envelope.received_at <= current:
            live.apply(envelope)
            envelope = next(pending, None)
        live_snapshots.extend(live.snapshots(current))

    replayed = tuple(
        snapshot
        for snapshot in replay_features(envelopes, configuration, trade_date=TRADE_DATE)
        if snapshot.decision_at <= decision_at
    )

    assert replayed == tuple(live_snapshots)


def test_future_observations_cannot_change_an_earlier_feature_snapshot() -> None:
    decision_at = _received(9, 20, 5)
    observations = _observations()
    future = (
        _trade(
            10,
            hour=9,
            minute=20,
            second=10,
            price=110,
            quantity=30,
            side="B",
            total_volume=100,
        ),
    )

    assert _snapshot_at(observations, decision_at) == _snapshot_at(
        observations + future,
        decision_at,
    )


def test_full_replay_exhausts_its_verified_input() -> None:
    exhausted = False

    def envelopes():
        nonlocal exhausted
        yield from _observations()
        exhausted = True

    replay_features(envelopes(), _configuration(), trade_date=TRADE_DATE)

    assert exhausted is True


def test_feature_audit_is_complete_reconciled_and_deterministic() -> None:
    configuration = _configuration()
    snapshots = replay_features(_observations(), configuration, trade_date=TRADE_DATE)

    def build():
        return build_feature_audit(
            snapshots,
            configuration,
            trade_date=TRADE_DATE,
            manifest_uri="s3://landing/stream/manifest.json",
            stream_session_id="session-1",
            input_message_count=9,
        )

    report = build()
    repeated = build()

    assert report.model_dump_json() == repeated.model_dump_json()
    assert report.snapshot_count == 5_396
    assert report.last_decision_receive_sequence == 9
    assert report.symbols["VIC"].snapshot_count == 2_698
    assert report.symbols["VIC"].eligible_count == 2
    assert report.symbols["VIC"].sessions["continuous_am"].eligible_count == 2
    assert report.symbols["VIC"].sessions["continuous_pm"].eligible_count == 0
    assert report.symbols["VIC"].reason_counts["STALE_QUOTE"] > 0
    assert (
        report.symbols["VIC"].eligible_distributions["spread_bps"].count
        == report.symbols["VIC"].eligible_count
    )
    trade_age = report.symbols["VIC"].eligible_distributions["trade_age_seconds"]
    assert (trade_age.minimum, trade_age.p50, trade_age.p95, trade_age.maximum) == (
        Decimal(10),
        Decimal("12.5"),
        Decimal("14.75"),
        Decimal(15),
    )
    assert report.symbols["VHM"].eligible_count == 0
    assert report.symbols["VHM"].null_rates["mid_price"] == Decimal(1)
    assert report.symbols["VHM"].eligible_distributions == {}


def test_feature_audit_rejects_incomplete_replay_coverage() -> None:
    configuration = _configuration()
    snapshots = replay_features(_observations(), configuration, trade_date=TRADE_DATE)

    with pytest.raises(ValueError, match="every configured decision key exactly once"):
        build_feature_audit(
            snapshots[:-1],
            configuration,
            trade_date=TRADE_DATE,
            manifest_uri="s3://landing/stream/manifest.json",
            stream_session_id="session-1",
            input_message_count=9,
        )
