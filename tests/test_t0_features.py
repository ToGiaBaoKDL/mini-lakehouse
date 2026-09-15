import hashlib
import json
import os
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from t0_trading.capture.reader import StreamDayReader, StreamGap
from t0_trading.configuration import load_configuration
from t0_trading.decisions import (
    DecisionEngine,
    ShadowDecisionJournal,
    ShadowJournalManifest,
    prune_shadow_journals,
    replay_decisions,
)
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


def _short_configuration():
    configuration = _configuration()
    sessions = configuration.market.sessions.model_copy(
        update={"continuous_am": (time(9, 15), time(9, 21))}
    )
    return configuration.model_copy(
        update={
            "market": configuration.market.model_copy(update={"sessions": sessions}),
            "features": configuration.features.model_copy(
                update={"decision_sessions": ("continuous_am",)}
            ),
        }
    )


def _capture(message_count: int) -> StreamDayReader:
    return cast(
        StreamDayReader,
        SimpleNamespace(
            trade_date=TRADE_DATE,
            manifest_uris=("s3://landing/stream/manifest.json",),
            stream_session_ids=("session-1",),
            evidence_sha256="a" * 64,
            message_count=message_count,
            session_message_counts={"session-1": message_count},
        ),
    )


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
    assert len(snapshot.sha256) == 64
    assert snapshot.sha256 == hashlib.sha256(snapshot.canonical_bytes()).hexdigest()


def test_live_clock_and_full_replay_emit_identical_point_in_time_snapshots() -> None:
    configuration = _configuration()
    loaded = load_configuration(CONFIGURATION)
    envelopes = _observations()
    decision_at = _received(9, 20, 5)
    live = FeatureEngine(configuration)
    live_decisions = DecisionEngine(
        configuration,
        loaded.resolve_strategies(TRADE_DATE),
        loaded.resolve_outcomes(TRADE_DATE),
        loaded.resolve_decisions(TRADE_DATE),
    )
    live_snapshots = []
    live_journal = []
    pending = iter(envelopes)
    envelope = next(pending, None)
    for current in decision_times(configuration, TRADE_DATE):
        if current > decision_at:
            break
        while envelope is not None and envelope.received_at <= current:
            live.apply(envelope)
            envelope = next(pending, None)
        current_snapshots = live.snapshots(current)
        live_snapshots.extend(current_snapshots)
        live_journal.extend(live_decisions.decisions(current_snapshots))

    replayed = tuple(
        snapshot
        for snapshot in replay_features(envelopes, configuration, trade_date=TRADE_DATE)
        if snapshot.decision_at <= decision_at
    )

    assert replayed == tuple(live_snapshots)
    assert replay_decisions(
        replayed,
        configuration,
        loaded.resolve_strategies(TRADE_DATE),
        loaded.resolve_outcomes(TRADE_DATE),
        loaded.resolve_decisions(TRADE_DATE),
    ) == tuple(live_journal)


def test_shadow_journal_matches_multi_segment_replay_and_commits_manifest(
    tmp_path: Path,
) -> None:
    loaded = load_configuration(CONFIGURATION)
    configuration = _short_configuration()
    first_segment = _observations()[:4]
    second_segment = tuple(
        envelope.model_copy(update={"stream_session_id": "session-2", "receive_sequence": sequence})
        for sequence, envelope in enumerate(_observations()[4:], start=1)
    )
    gap = StreamGap(
        started_at=_received(9, 16, 0),
        ended_at=_received(9, 19, 30),
    )
    capture_manifests = (
        "s3://landing/stream/trade_date=2026-09-04/session=session-1/manifest.json",
        "s3://landing/stream/trade_date=2026-09-04/session=session-2/manifest.json",
    )
    output = tmp_path / "shadow.jsonl"
    journal = ShadowDecisionJournal(
        output,
        TRADE_DATE,
        configuration,
        loaded.resolve_strategies(TRADE_DATE),
        loaded.resolve_outcomes(TRADE_DATE),
        loaded.resolve_decisions(TRADE_DATE),
    )
    journal.connected("session-1", _received(9, 0, 0))
    for envelope in first_segment:
        journal.ingest((envelope,))
        journal.advance(envelope.received_at + timedelta(seconds=5))
    journal.disconnected(gap.started_at, unavailable=True)
    journal.connected("session-2", gap.ended_at)
    for envelope in second_segment:
        journal.ingest((envelope,))
        journal.advance(envelope.received_at + timedelta(seconds=5))
    journal.close(_received(9, 21, 0), capture_manifests)

    expected = replay_decisions(
        replay_features(
            (*first_segment, *second_segment),
            configuration,
            trade_date=TRADE_DATE,
            gaps=(gap,),
        ),
        configuration,
        loaded.resolve_strategies(TRADE_DATE),
        loaded.resolve_outcomes(TRADE_DATE),
        loaded.resolve_decisions(TRADE_DATE),
    )
    expected_body = b"".join(decision.canonical_bytes() + b"\n" for decision in expected)
    manifest = ShadowJournalManifest.model_validate_json(journal.manifest_output.read_bytes())

    assert output.read_bytes() == expected_body
    assert journal.decision_count == len(expected)
    assert journal.journal_sha256 == hashlib.sha256(expected_body).hexdigest()
    assert manifest.capture_manifest_uris == capture_manifests
    assert manifest.stream_session_ids == ("session-1", "session-2")
    assert manifest.decision_count == len(expected)
    assert manifest.journal_sha256 == journal.journal_sha256
    assert journal.manifest is not None
    assert manifest.sha256 == journal.manifest.sha256
    assert any("CAPTURE_GAP" in decision.reasons for decision in expected)
    assert not journal.partial_output.exists()
    assert not journal.partial_manifest_output.exists()


@pytest.mark.parametrize(
    ("completed_at", "capture_manifests"),
    (
        (
            _received(9, 20, 5),
            ("s3://landing/stream/trade_date=2026-09-04/session=session-1/manifest.json",),
        ),
        (_received(9, 21, 0), ()),
    ),
)
def test_shadow_journal_requires_every_decision_clock_and_terminal_manifest(
    tmp_path: Path,
    completed_at: datetime,
    capture_manifests: tuple[str, ...],
) -> None:
    loaded = load_configuration(CONFIGURATION)
    configuration = _short_configuration()
    output = tmp_path / "shadow.jsonl"
    journal = ShadowDecisionJournal(
        output,
        TRADE_DATE,
        configuration,
        loaded.resolve_strategies(TRADE_DATE),
        loaded.resolve_outcomes(TRADE_DATE),
        loaded.resolve_decisions(TRADE_DATE),
    )
    journal.connected("session-1", _received(9, 0, 0))
    journal.close(completed_at, capture_manifests)

    assert journal.failed is True
    assert journal.partial_output.exists()
    assert not output.exists()
    assert not journal.manifest_output.exists()


def test_shadow_journal_fails_closed_without_raising_into_capture(tmp_path: Path) -> None:
    loaded = load_configuration(CONFIGURATION)
    configuration = loaded.resolve(TRADE_DATE)
    failures: list[str] = []

    def failing_error_callback(error: Exception) -> None:
        failures.append(type(error).__name__)
        raise RuntimeError("observer reporting must not reach capture")

    output = tmp_path / "shadow.jsonl"
    journal = ShadowDecisionJournal(
        output,
        TRADE_DATE,
        configuration,
        loaded.resolve_strategies(TRADE_DATE),
        loaded.resolve_outcomes(TRADE_DATE),
        loaded.resolve_decisions(TRADE_DATE),
        on_error=failing_error_callback,
    )

    journal.ingest((_observations()[1], _observations()[0]))
    journal.advance(_received(9, 20, 5))

    assert journal.failed is True
    assert failures == ["ValueError"]
    assert journal.partial_output.exists()
    assert not output.exists()


def test_shadow_journal_initialization_failure_leaves_no_artifact(tmp_path: Path) -> None:
    loaded = load_configuration(CONFIGURATION)
    invalid_policy = loaded.resolve_decisions(TRADE_DATE).model_copy(
        update={"strategy_version": "missing-strategy"}
    )

    with pytest.raises(ValueError, match="lineage"):
        ShadowDecisionJournal(
            tmp_path / "shadow.jsonl",
            TRADE_DATE,
            _configuration(),
            loaded.resolve_strategies(TRADE_DATE),
            loaded.resolve_outcomes(TRADE_DATE),
            invalid_policy,
        )

    assert not any(tmp_path.iterdir())


def test_shadow_journal_rejects_a_late_first_connection(tmp_path: Path) -> None:
    loaded = load_configuration(CONFIGURATION)
    journal = ShadowDecisionJournal(
        tmp_path / "shadow.jsonl",
        TRADE_DATE,
        _short_configuration(),
        loaded.resolve_strategies(TRADE_DATE),
        loaded.resolve_outcomes(TRADE_DATE),
        loaded.resolve_decisions(TRADE_DATE),
    )

    journal.connected("session-1", _received(9, 15, 6))

    assert journal.failed is True
    assert journal.partial_output.exists()
    assert not journal.output.exists()


def test_shadow_journal_retention_is_scoped_by_artifact_state(tmp_path: Path) -> None:
    now = datetime(2026, 9, 16, tzinfo=UTC)
    old_completed = (
        tmp_path / "old.jsonl",
        tmp_path / "old.manifest.json",
    )
    old_partial = (
        tmp_path / "failed.jsonl.partial",
        tmp_path / "failed.manifest.json.partial",
    )
    retained = (
        tmp_path / "recent.jsonl",
        tmp_path / "recent.manifest.json",
        tmp_path / "recent.jsonl.partial",
        tmp_path / "unowned.txt",
    )
    for path in (*old_completed, *old_partial, *retained):
        path.touch()
    for path in old_completed:
        timestamp = (now - timedelta(days=15)).timestamp()
        os.utime(path, (timestamp, timestamp))
    for path in old_partial:
        timestamp = (now - timedelta(days=4)).timestamp()
        os.utime(path, (timestamp, timestamp))

    prune_shadow_journals(tmp_path, observed_at=now)

    assert all(not path.exists() for path in (*old_completed, *old_partial))
    assert all(path.exists() for path in retained)


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


def test_replay_fails_closed_only_during_gap_recovery_window() -> None:
    configuration = _configuration()
    observations = tuple(
        envelope.model_copy(
            update={
                "stream_session_id": "session-2",
                "receive_sequence": index - 5,
            }
        )
        if envelope.received_at >= _received(9, 19, 45)
        else envelope
        for index, envelope in enumerate(_observations(), start=1)
    )
    gap = StreamGap(
        started_at=_received(9, 19, 42),
        ended_at=_received(9, 19, 44),
    )

    snapshots = replay_features(
        observations,
        configuration,
        trade_date=TRADE_DATE,
        gaps=(gap,),
    )
    affected = next(
        snapshot
        for snapshot in snapshots
        if snapshot.symbol == "VIC" and snapshot.decision_at == _received(9, 20, 5)
    )

    assert affected.stream_session_id == "session-2"
    assert "CAPTURE_GAP" in affected.reasons


def test_feature_audit_is_complete_reconciled_and_deterministic() -> None:
    configuration = _configuration()
    snapshots = replay_features(_observations(), configuration, trade_date=TRADE_DATE)

    def build():
        return build_feature_audit(
            snapshots,
            configuration,
            capture=_capture(9),
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
            capture=_capture(9),
        )
