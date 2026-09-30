"""Constituent facts and live/replay breadth must preserve point-in-time evidence."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from zoneinfo import ZoneInfo

import pytest
from emr_jobs.market_data.constituents import bar_rows
from t0_trading.capture.membership import BreadthMembershipSnapshot, capture_index_memberships
from t0_trading.capture.reader import StreamSessionReader
from t0_trading.configuration import BreadthVersion, load_configuration
from t0_trading.context import LiveDecisionContextEngine, build_decision_contexts
from t0_trading.context.breadth import BreadthEngine
from t0_trading.features import FeatureEngine
from t0_trading.identity import canonical_json, sha256
from t0_trading.market.events import StreamEnvelope
from t0_trading.market.intervals import provider_bar

from lakehouse.contracts import load_contracts

CONFIG = Path("t0-trading/config/trading.yaml")
AT = datetime(2026, 10, 2, 2, 30, tzinfo=UTC)
ZONE = ZoneInfo("Asia/Ho_Chi_Minh")


def _membership(at: datetime = AT - timedelta(hours=1)) -> BreadthMembershipSnapshot:
    class Market:
        def get_securities_info_by_index(self, index: str) -> object:
            symbols = ("FPT", "VHM", "VIC") if index == "VN30" else ("VHM", "VIC")
            return [{"symbol": symbol} for symbol in symbols]

    return capture_index_memberships(Market(), ("VN30", "VNREAL"), clock=lambda: at)


def _policy(**updates: object) -> BreadthVersion:
    policy = load_configuration(CONFIG).resolve_breadth(AT.date())
    assert policy is not None
    return BreadthVersion.model_validate({**policy.model_dump(), **updates})


def _interval(
    sequence: int,
    symbol: str,
    minute: datetime,
    close: int,
    *,
    received_at: datetime | None = None,
    observed_second: int = 59,
) -> StreamEnvelope:
    observed = minute + timedelta(seconds=observed_second)
    payload = {
        "type": "trade",
        "symbol": symbol,
        "interval_time": minute.astimezone(ZONE).strftime("%Y/%m/%d %H:%M:%S"),
        "trading_time": observed.astimezone(ZONE).strftime("%Y/%m/%d %H:%M:%S"),
        "open": 10000,
        "high": max(10000, close),
        "low": min(10000, close),
        "close": close,
        "volume": 100,
    }
    body = canonical_json(payload).decode()
    return StreamEnvelope(
        stream_session_id="segment-1",
        receive_sequence=sequence,
        message_type="IntervalMessage",
        symbol=symbol,
        source_time_text=str(payload["trading_time"]),
        received_at=received_at or observed,
        message_json=body,
        message_sha256=sha256(body.encode()),
    )


def _evidence() -> tuple[StreamEnvelope, ...]:
    return tuple(
        _interval(number, symbol, AT - timedelta(minutes=offset), close)
        for number, (offset, symbol, close) in enumerate(
            [(6, symbol, 10000) for symbol in ("FPT", "VHM", "VIC")]
            + [(1, "FPT", 9900), (1, "VHM", 10100), (1, "VIC", 10100)],
            start=1,
        )
    )


def _engine(
    policy: BreadthVersion | None = None, membership: BreadthMembershipSnapshot | None = None
) -> BreadthEngine:
    return BreadthEngine(
        load_configuration(CONFIG).resolve(AT.date()),
        policy or _policy(),
        membership or _membership(),
    )


def test_equal_weight_breadth_and_sector_confirmation() -> None:
    engine = _engine()
    for item in _evidence():
        engine.apply(item)
    broad, sector = engine.build(AT)
    assert (broad.index, sector.index) == ("VN30", "VNREAL")
    assert broad.participation == 1
    assert broad.advance_ratio == Decimal("0.66666667")
    assert broad.mean_return_bps == Decimal("33.33333333")
    assert broad.dispersion_bps == Decimal("94.28090416")
    assert broad.upward_confirmation is True
    assert sector.advance_ratio == 1
    assert sector.mean_return_bps == 100
    assert sector.dispersion_bps == 0
    assert sector.upward_confirmation is True
    assert sector.membership_sha256 == _membership().snapshot_sha256
    assert sector.reasons == ()


def test_missing_constituents_keep_the_full_denominator() -> None:
    engine = _engine()
    for item in _evidence():
        if item.symbol != "FPT":
            engine.apply(item)
    broad, sector = engine.build(AT)
    assert broad.expected_count == 3
    assert broad.observed_count == 2
    assert broad.advance_ratio == 1  # Partial metric is inspectable, not usable confirmation.
    assert broad.participation == Decimal("0.66666667")
    assert broad.upward_confirmation is None
    assert broad.reasons == ("insufficient_participation",)
    assert sector.upward_confirmation is True


def test_closed_minutes_only_and_late_revisions_are_receipt_gated() -> None:
    engine = _engine()
    for item in _evidence():
        engine.apply(item)
    before = engine.build(AT)
    engine.apply(_interval(7, "VIC", AT, 5000, observed_second=1))
    assert engine.build(AT + timedelta(seconds=5)) == before
    engine.apply(
        _interval(8, "VIC", AT - timedelta(minutes=1), 9900, received_at=AT + timedelta(seconds=6))
    )
    after = engine.build(AT + timedelta(seconds=10))
    assert after[1].advance_ratio == Decimal("0.5")
    assert after[1].upward_confirmation is False
    assert after[1].observations_sha256 != before[1].observations_sha256


def test_late_older_provider_update_cannot_rewind_latest_revision() -> None:
    engine = _engine()
    for item in _evidence():
        engine.apply(item)
    before = engine.build(AT)
    engine.apply(
        _interval(
            7,
            "VIC",
            AT - timedelta(minutes=1),
            9000,
            observed_second=10,
            received_at=AT + timedelta(seconds=1),
        )
    )
    assert engine.build(AT + timedelta(seconds=5)) == before


def test_no_forward_fill_and_stale_provider_values_do_not_count() -> None:
    engine = _engine(_policy(stale_after_seconds=60))
    for item in _evidence():
        engine.apply(item)
    stale = engine.build(AT + timedelta(seconds=61))
    assert all(item.observed_count == 0 for item in stale)
    assert all(item.upward_confirmation is None for item in stale)


def test_receiving_an_old_update_now_does_not_make_its_provider_price_fresh() -> None:
    engine = _engine()
    for sequence, offset in enumerate((6, 1), start=1):
        engine.apply(
            _interval(
                sequence,
                "VIC",
                AT - timedelta(minutes=offset),
                10100,
                observed_second=1,
                received_at=AT,
            )
        )
    result = engine.build(AT + timedelta(seconds=40))
    assert all(item.observed_count == 0 for item in result)


def test_breadth_scope_cannot_exceed_membership_capture() -> None:
    config = load_configuration(CONFIG)
    payload = config.model_dump()
    payload["breadth"] = [_policy(indices=("VNINDEX",)).model_dump()]
    with pytest.raises(ValueError, match="require captured membership"):
        type(config).model_validate(payload)


@pytest.mark.parametrize("at", [AT - timedelta(days=1), AT + timedelta(seconds=1)])
def test_membership_must_be_known_and_from_the_same_day(at: datetime) -> None:
    engine = _engine(membership=_membership(at))
    for item in _evidence():
        engine.apply(item)
    assert all(
        item.membership_sha256 is None and "membership_unavailable" in item.reasons
        for item in engine.build(AT)
    )


@pytest.mark.parametrize("hour,minute", [(2, 19), (6, 3), (5, 0), (7, 31)])
def test_lookback_does_not_cross_auction_lunch_or_closed_session(hour: int, minute: int) -> None:
    at = AT.replace(hour=hour, minute=minute)
    contexts = _engine().build(at)
    assert all("outside_continuous_lookback" in item.reasons for item in contexts)


def test_missing_membership_is_explicit_without_blocking_official_regime() -> None:
    config = load_configuration(CONFIG)
    engine = BreadthEngine(config.resolve(AT.date()), _policy(), None)
    assert all(item.expected_count == 0 and item.participation is None for item in engine.build(AT))


def test_replay_is_byte_identical_to_live_and_future_receipts_cannot_leak() -> None:
    config = load_configuration(CONFIG)
    version, context_policy = config.resolve(AT.date()), config.resolve_context(AT.date())
    policy, membership = _policy(), _membership()
    engine = LiveDecisionContextEngine(
        version, context_policy, breadth_policy=policy, breadth_membership=membership
    )
    features = FeatureEngine(version)
    snapshots = features.snapshots(AT)
    source = _evidence()
    for item in source:
        engine.apply(item)
    live = engine.build(snapshots)
    future = _interval(
        7, "VIC", AT - timedelta(minutes=1), 9000, received_at=AT + timedelta(seconds=1)
    )
    replayed = build_decision_contexts(
        snapshots,
        (*source, future),
        version,
        context_policy,
        breadth_policy=policy,
        breadth_membership=membership,
    )
    assert replayed[0].canonical_bytes() == live.canonical_bytes()
    without_breadth = build_decision_contexts(snapshots, source, version, context_policy)[0]
    assert live.regime == without_breadth.regime
    assert live.reasons == without_breadth.reasons
    assert "breadth" not in without_breadth.model_dump(mode="json")
    assert without_breadth.sha256 == sha256(
        canonical_json(without_breadth.model_dump(mode="json", exclude={"breadth"}))
    )


def test_prospective_policy_does_not_rewrite_existing_context_policy() -> None:
    config = load_configuration(CONFIG)
    assert config.resolve_breadth(date(2026, 10, 1)) is None
    assert config.resolve_breadth(date(2026, 10, 2)) is not None
    assert (
        config.resolve_context(date(2026, 10, 1)).sha256
        == config.resolve_context(date(2026, 10, 2)).sha256
    )


def test_breadth_rejects_out_of_order_and_future_state() -> None:
    engine = _engine()
    engine.apply(_evidence()[-1])
    with pytest.raises(ValueError, match="receipt ordered"):
        engine.apply(_evidence()[0])
    with pytest.raises(ValueError, match="before applied receipts"):
        engine.build(AT - timedelta(minutes=1))


def test_shared_sdk_parser_rejects_invalid_scope_and_corrupt_payload() -> None:
    envelope = _evidence()[0]
    with pytest.raises(ValueError, match="lineage"):
        provider_bar(envelope.model_copy(update={"subscription_context": "indices"}), ZONE)
    with pytest.raises(ValueError, match="checksum"):
        provider_bar(envelope.model_copy(update={"message_sha256": "0" * 64}), ZONE)


def test_curated_rows_preserve_revisions_membership_receipts_and_idempotency() -> None:
    membership = _membership()
    capture = cast(
        StreamSessionReader,
        SimpleNamespace(
            manifest=SimpleNamespace(breadth_membership=membership, stream_session_id="segment-1"),
            trade_date=AT.date(),
            uri="s3://landing/manifest.json",
            manifest_sha256="a" * 64,
        ),
    )
    source = (*_evidence(), _interval(7, "VIC", AT - timedelta(minutes=1), 10200, received_at=AT))
    first = tuple(bar_rows(source, capture, timezone=ZONE, processed_at=AT))
    second = tuple(bar_rows(source, capture, timezone=ZONE, processed_at=AT + timedelta(hours=1)))
    assert len(first) == len(source)
    assert [row["source_record_sha256"] for row in first] == [
        row["source_record_sha256"] for row in second
    ]
    assert first[-1]["received_at"] == AT
    assert first[-1]["membership_sha256"] == membership.snapshot_sha256
    assert first[-1]["source_kind"] == "ssi_stream_interval"
    assert first[-1]["source_record_sha256"] != first[-2]["source_record_sha256"]
    table = load_contracts().curated_product("market_data").table("constituent_bars_1m")
    assert set(first[0]) == {column.name for column in table.columns}
    assert len({tuple(row[key] for key in table.primary_key) for row in first}) == len(first)
    assert (
        tuple(
            bar_rows(
                (_interval(8, "UNSCOPED", AT, 10000),), capture, timezone=ZONE, processed_at=AT
            )
        )
        == ()
    )
