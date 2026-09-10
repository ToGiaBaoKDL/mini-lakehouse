import gzip
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from ssi_sdk import __version__ as SSI_SDK_VERSION
from t0_trading.capture import MAX_STREAM_BATCH_MESSAGES, SSI_STREAM_RAW_PREFIX
from t0_trading.capture.reader import (
    StreamBatch,
    StreamCaptureReadError,
    StreamSessionReader,
    stream_manifest_uris,
)
from t0_trading.configuration import load_configuration
from t0_trading.identity import canonical_json, sha256
from t0_trading.market.reconciliation import (
    certify_market_day,
    reconcile_session,
    reconcile_trade_date,
)
from t0_trading.market.session import (
    MarketSession,
    covers_trading_window,
    session_at,
    trading_window,
)
from t0_trading.provider import SSI_API_VERSION

SESSION_ID = "39daeb94-73ad-4f3f-a40c-7f045697dce2"
TRADE_DATE = date(2026, 9, 4)
SESSION_PREFIX = f"{SSI_STREAM_RAW_PREFIX}/trade_date={TRADE_DATE.isoformat()}/session={SESSION_ID}"


class _Store:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def uri(self, key: str) -> str:
        return f"s3://landing/{key}"

    def read_json(self, key: str) -> dict[str, Any] | None:
        body = self.objects.get(key)
        if body is None:
            return None
        value = json.loads(body)
        assert isinstance(value, dict)
        return value

    def read_capture(self, key: str) -> bytes | None:
        return self.objects.get(key)


class _Paginator:
    def __init__(self, pages: list[dict[str, object]]) -> None:
        self.pages = pages

    def paginate(self, **kwargs: str) -> list[dict[str, object]]:
        assert kwargs == {
            "Bucket": "landing",
            "Prefix": ("root/stream/ssi_fastconnect_stream/raw/trade_date=2026-09-04/"),
        }
        return self.pages


class _S3:
    def __init__(self, pages: list[dict[str, object]]) -> None:
        self.paginator = _Paginator(pages)

    def get_paginator(self, name: str) -> _Paginator:
        assert name == "list_objects_v2"
        return self.paginator


def _captured_row(
    sequence: int,
    message_type: str,
    payload: dict[str, object],
    received_at: datetime,
) -> dict[str, object]:
    message_json = canonical_json(payload).decode()
    return {
        "stream_session_id": SESSION_ID,
        "receive_sequence": sequence,
        "message_type": message_type,
        "subscription_context": "symbols",
        "provider_topic": None,
        "symbol": payload.get("symbol"),
        "source_time_text": payload.get("trading_time"),
        "received_at": received_at.isoformat(),
        "message_json": message_json,
        "message_sha256": sha256(message_json.encode()),
        "api_version": SSI_API_VERSION,
        "sdk_version": SSI_SDK_VERSION,
    }


def _trade_row(
    sequence: int,
    *,
    symbol: str,
    trading_time: str,
    price: int,
    quantity: int,
    side: str,
    total_volume: int,
    received_at: datetime,
) -> dict[str, object]:
    return _captured_row(
        sequence,
        "TradeMessage",
        {
            "type": "trade",
            "symbol": symbol,
            "trading_time": trading_time,
            "price": price,
            "quantity": quantity,
            "side": side,
            "total_volume": total_volume,
        },
        received_at,
    )


def _interval_row(
    sequence: int,
    *,
    symbol: str = "VIC",
    interval_time: str = "2026/09/04 09:15:00",
    observed_time: str,
    open_price: int = 100,
    close: int,
    high: int,
    low: int = 100,
    volume: int,
    received_at: datetime,
) -> dict[str, object]:
    return _captured_row(
        sequence,
        "IntervalMessage",
        {
            "type": "trade",
            "interval_time": interval_time,
            "trading_time": observed_time,
            "symbol": symbol,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        received_at,
    )


def _quote_row(
    sequence: int,
    *,
    symbol: str,
    trading_time: str,
    received_at: datetime,
) -> dict[str, object]:
    return _captured_row(
        sequence,
        "QuoteMessage",
        {
            "type": "quote",
            "symbol": symbol,
            "trading_time": trading_time,
            "bid_prices": [99, 98, 97] + [0] * 7,
            "bid_volumes": [10, 20, 30] + [0] * 7,
            "ask_prices": [101, 102, 103] + [0] * 7,
            "ask_volumes": [11, 21, 31] + [0] * 7,
        },
        received_at,
    )


def _reader(
    *,
    interval_close: int = 102,
    interval_high: int = 102,
    interval_volume: int = 30,
    symbols: tuple[str, ...] = ("VIC", "VHM"),
    include_vhm: bool = True,
    interval_observed_time: str = "2026/09/04 09:15:57",
    first_total_volume: int = 10,
    corrupt: bool = False,
    connected_at: str = "2026-09-04T01:00:00+00:00",
    disconnected_at: str = "2026-09-04T09:00:00+00:00",
    disconnect_kind: str = "shutdown",
    error_type: str | None = None,
) -> StreamSessionReader:
    rows = [
        _quote_row(
            1,
            symbol="VIC",
            trading_time="2026/09/04 09:15:01",
            received_at=datetime(2026, 9, 4, 2, 15, 2, tzinfo=UTC),
        )
    ]
    if include_vhm:
        rows.append(
            _quote_row(
                len(rows) + 1,
                symbol="VHM",
                trading_time="2026/09/04 09:15:02",
                received_at=datetime(2026, 9, 4, 2, 15, 3, tzinfo=UTC),
            )
        )
    rows.append(
        _trade_row(
            len(rows) + 1,
            symbol="VIC",
            trading_time="2026/09/04 09:15:05",
            price=100,
            quantity=10,
            side="B",
            total_volume=first_total_volume,
            received_at=datetime(2026, 9, 4, 2, 15, 6, tzinfo=UTC),
        )
    )
    if include_vhm:
        rows.extend(
            (
                _trade_row(
                    len(rows) + 1,
                    symbol="VHM",
                    trading_time="2026/09/04 09:15:10",
                    price=200,
                    quantity=5,
                    side="B",
                    total_volume=5,
                    received_at=datetime(2026, 9, 4, 2, 15, 11, tzinfo=UTC),
                ),
                _interval_row(
                    len(rows) + 2,
                    symbol="VHM",
                    observed_time="2026/09/04 09:15:20",
                    open_price=200,
                    high=200,
                    low=200,
                    close=200,
                    volume=5,
                    received_at=datetime(2026, 9, 4, 2, 15, 21, tzinfo=UTC),
                ),
            )
        )
    rows.append(
        _trade_row(
            len(rows) + 1,
            symbol="VIC",
            trading_time="2026/09/04 09:15:40",
            price=102,
            quantity=20,
            side="S",
            total_volume=first_total_volume + 20,
            received_at=datetime(2026, 9, 4, 2, 15, 41, tzinfo=UTC),
        )
    )
    rows.append(
        _interval_row(
            len(rows) + 1,
            observed_time=interval_observed_time,
            close=interval_close,
            high=interval_high,
            volume=interval_volume,
            received_at=datetime(2026, 9, 4, 2, 15, 58, tzinfo=UTC),
        )
    )
    return _reader_from_rows(
        rows,
        symbols=symbols,
        corrupt=corrupt,
        connected_at=connected_at,
        disconnected_at=disconnected_at,
        disconnect_kind=disconnect_kind,
        error_type=error_type,
    )


def _reader_from_rows(
    rows: list[dict[str, object]],
    *,
    symbols: tuple[str, ...] = ("VIC", "VHM"),
    batch_published_at: str = "2026-09-04T09:00:00+00:00",
    corrupt: bool = False,
    connected_at: str = "2026-09-04T01:00:00+00:00",
    disconnected_at: str = "2026-09-04T09:00:00+00:00",
    disconnect_kind: str = "shutdown",
    error_type: str | None = None,
) -> StreamSessionReader:
    body = gzip.compress(
        b"".join(canonical_json(row) + b"\n" for row in rows),
        mtime=0,
    )
    digest = sha256(body)
    message_count = len(rows)
    object_key = f"{SESSION_PREFIX}/batches/000000000001-{message_count:012d}-{digest}.json.gz"
    batch = {
        "batch_id": sha256(
            canonical_json(
                {
                    "stream_session_id": SESSION_ID,
                    "first_receive_sequence": 1,
                    "last_receive_sequence": message_count,
                    "object_sha256": digest,
                }
            )
        ),
        "first_receive_sequence": 1,
        "last_receive_sequence": message_count,
        "message_count": message_count,
        "object_key": object_key,
        "object_sha256": digest,
        "published_at": batch_published_at,
    }
    manifest_key = f"{SESSION_PREFIX}/manifest.json"
    manifest = {
        "schema_version": 1,
        "stream_session_id": SESSION_ID,
        "symbols": list(symbols),
        "connected_at": connected_at,
        "disconnected_at": disconnected_at,
        "disconnect_kind": disconnect_kind,
        "message_count": message_count,
        "first_receive_sequence": 1,
        "last_receive_sequence": message_count,
        "heartbeat_count": 960,
        "last_heartbeat_at": "2026-09-04T08:59:59+00:00",
        "last_business_message_at": rows[-1]["received_at"],
        "batch_count": 1,
        "batches": [batch],
        "api_version": SSI_API_VERSION,
        "sdk_version": SSI_SDK_VERSION,
        "error_type": error_type,
        "published_at": "2026-09-04T09:00:01+00:00",
    }
    objects = {object_key: body, manifest_key: canonical_json(manifest)}
    reader = StreamSessionReader(_Store(objects), manifest_key)
    if corrupt:
        objects[object_key] += b"corrupt"
    return reader


def test_market_sessions_use_configured_exchange_local_boundaries() -> None:
    version = load_configuration(Path("t0-trading/config/trading.yaml")).resolve(TRADE_DATE)
    timezone = ZoneInfo(version.market.timezone)
    market_open, market_close = trading_window(
        TRADE_DATE,
        timezone=timezone,
        schedule=version.market.sessions,
    )

    assert market_open == datetime(2026, 9, 4, 2, 0, tzinfo=UTC)
    assert market_close == datetime(2026, 9, 4, 7, 45, tzinfo=UTC)
    assert covers_trading_window(
        datetime(2026, 9, 4, 1, 0, tzinfo=UTC),
        datetime(2026, 9, 4, 9, 0, tzinfo=UTC),
        trade_date=TRADE_DATE,
        timezone=timezone,
        schedule=version.market.sessions,
    )
    assert not covers_trading_window(
        datetime(2026, 9, 4, 2, 1, tzinfo=UTC),
        datetime(2026, 9, 4, 9, 0, tzinfo=UTC),
        trade_date=TRADE_DATE,
        timezone=timezone,
        schedule=version.market.sessions,
    )
    assert (
        session_at(
            datetime(2026, 9, 4, 4, 30, tzinfo=UTC),
            trade_date=TRADE_DATE,
            timezone=timezone,
            schedule=version.market.sessions,
        )
        == MarketSession.LUNCH_BREAK
    )
    assert (
        session_at(
            datetime(2026, 9, 4, 7, 45, tzinfo=UTC),
            trade_date=TRADE_DATE,
            timezone=timezone,
            schedule=version.market.sessions,
        )
        == MarketSession.CLOSING_AUCTION
    )
    assert (
        session_at(
            datetime(2026, 9, 4, 7, 45, 1, tzinfo=UTC),
            trade_date=TRADE_DATE,
            timezone=timezone,
            schedule=version.market.sessions,
        )
        == MarketSession.CLOSED
    )


def test_manifest_discovery_returns_only_direct_terminal_manifests() -> None:
    prefix = "root/stream/ssi_fastconnect_stream/raw/trade_date=2026-09-04/"
    manifest = f"{prefix}session={SESSION_ID}/manifest.json"
    client = _S3(
        [
            {
                "Contents": [
                    {"Key": manifest},
                    {"Key": f"{prefix}session={SESSION_ID}/batches/part.json.gz"},
                    {"Key": f"{prefix}session={SESSION_ID}/nested/manifest.json"},
                ]
            },
            {"Contents": [{"Key": manifest}]},
        ]
    )

    assert stream_manifest_uris(client, "s3://landing/root", TRADE_DATE) == (
        f"s3://landing/{manifest}",
    )


def test_trade_date_reconciliation_requires_one_market_window_session() -> None:
    reader = _reader()
    version = load_configuration(Path("t0-trading/config/trading.yaml")).resolve(TRADE_DATE)

    assert reconcile_trade_date((reader,), version, trade_date=TRADE_DATE).status == "passed"
    with pytest.raises(ValueError, match="no terminal"):
        reconcile_trade_date((), version, trade_date=TRADE_DATE)
    with pytest.raises(ValueError, match="found 2"):
        reconcile_trade_date((reader, reader), version, trade_date=TRADE_DATE)


def test_trade_date_reconciliation_ignores_partial_recovery_sessions() -> None:
    version = load_configuration(Path("t0-trading/config/trading.yaml")).resolve(TRADE_DATE)
    full = _reader(disconnect_kind="capture_error", error_type="WebSocketError")
    partial = _reader(connected_at="2026-09-04T02:10:00+00:00")

    report = reconcile_trade_date((full, partial), version, trade_date=TRADE_DATE)

    assert report.status == "passed"
    assert report.stream_session_id == full.manifest.stream_session_id
    assert report.disconnect_kind == "capture_error"
    assert report.error_type == "WebSocketError"


def test_market_day_certification_preserves_evidence_but_rejects_partial_captures() -> None:
    version = load_configuration(Path("t0-trading/config/trading.yaml")).resolve(TRADE_DATE)
    fragments = (
        _reader(connected_at="2026-09-04T02:10:00+00:00"),
        _reader(connected_at="2026-09-04T02:11:00+00:00"),
        _reader(connected_at="2026-09-04T02:12:00+00:00"),
    )

    certification, report = certify_market_day(fragments, version, trade_date=TRADE_DATE)

    assert certification.status == "failed"
    assert certification.failure_reason == "no_full_session"
    assert certification.manifest_count == 3
    assert certification.full_window_session_count == 0
    assert certification.eligible_session_count == 0
    assert certification.selected_stream_session_id is None
    assert report is None
    reordered, _ = certify_market_day(
        tuple(reversed(fragments)),
        version,
        trade_date=TRADE_DATE,
    )
    assert certification.evidence_sha256 == reordered.evidence_sha256


def test_market_day_certification_rejects_a_full_capture_with_the_wrong_scope() -> None:
    version = load_configuration(Path("t0-trading/config/trading.yaml")).resolve(TRADE_DATE)

    certification, report = certify_market_day(
        (_reader(symbols=("VIC",), include_vhm=False),),
        version,
        trade_date=TRADE_DATE,
    )

    assert certification.status == "failed"
    assert certification.failure_reason == "capture_scope_mismatch"
    assert certification.full_window_session_count == 1
    assert certification.eligible_session_count == 0
    assert report is None


def test_reader_streams_verified_rows_and_reconciliation_matches_ohlcv() -> None:
    reader = _reader()
    assert [row.receive_sequence for row in reader.envelopes()] == list(range(1, 8))

    version = load_configuration(Path("t0-trading/config/trading.yaml")).resolve(TRADE_DATE)
    report = reconcile_session(reader, version)

    assert report.status == "passed"
    assert report.full_session_coverage is True
    assert report.capture_scope_matches_configuration is True
    assert report.trade_symbols == ("VHM", "VIC")
    assert report.quote_symbols == ("VHM", "VIC")
    assert report.cumulative_volume_baseline_matches is True
    assert report.out_of_session_trade_count == 0
    assert report.message_counts == {"IntervalMessage": 2, "QuoteMessage": 2, "TradeMessage": 3}
    assert report.event_counts == {"QuoteSnapshot": 2, "Trade": 3}
    assert report.session_counts == {"continuous_am": 5}
    assert report.replayed_bar_count == 2
    assert report.provider_interval_update_count == 2
    assert report.provider_interval_minute_count == 2
    assert report.matched_interval_update_count == 2
    assert report.final_interval_exact_minute_count == 2
    assert report.provider_interval_progression_issue_count == 0
    assert report.differences == ()


def test_reconciliation_fails_closed_on_an_ohlcv_difference() -> None:
    version = load_configuration(Path("t0-trading/config/trading.yaml")).resolve(TRADE_DATE)
    report = reconcile_session(_reader(interval_close=101), version)

    assert report.status == "failed"
    assert report.matched_interval_update_count == 1
    assert report.final_interval_exact_minute_count == 1
    assert report.differences == (
        "VIC@2026-09-04T09:15:00+07:00:interval_not_causal_trade_prefix["
        "observed_at=2026-09-04T09:15:57+07:00]",
    )


def test_market_day_certification_withholds_a_session_that_fails_reconciliation() -> None:
    version = load_configuration(Path("t0-trading/config/trading.yaml")).resolve(TRADE_DATE)

    certification, report = certify_market_day(
        (_reader(interval_close=101),),
        version,
        trade_date=TRADE_DATE,
    )

    assert report is not None and report.status == "failed"
    assert certification.status == "failed"
    assert certification.failure_reason == "reconciliation_failed"
    assert certification.eligible_session_count == 1
    assert certification.selected_stream_session_id is None


def test_reconciliation_accepts_a_provider_interval_that_precedes_the_last_trade() -> None:
    version = load_configuration(Path("t0-trading/config/trading.yaml")).resolve(TRADE_DATE)
    report = reconcile_session(
        _reader(
            interval_close=100,
            interval_high=100,
            interval_volume=10,
            interval_observed_time="2026/09/04 09:15:20",
        ),
        version,
    )

    assert report.status == "passed"
    assert report.matched_interval_update_count == 2
    assert report.final_interval_exact_minute_count == 1
    assert report.differences == ()


def test_reconciliation_fails_when_a_configured_symbol_has_no_business_data() -> None:
    version = load_configuration(Path("t0-trading/config/trading.yaml")).resolve(TRADE_DATE)
    report = reconcile_session(_reader(include_vhm=False), version)

    assert report.status == "failed"
    assert report.capture_scope_matches_configuration is True
    assert report.trade_symbols == ("VIC",)
    assert report.quote_symbols == ("VIC",)
    assert report.differences == (
        "VHM:configured_symbol_without_quote",
        "VHM:configured_symbol_without_trade",
    )


def test_reconciliation_fails_when_capture_scope_omits_a_configured_symbol() -> None:
    version = load_configuration(Path("t0-trading/config/trading.yaml")).resolve(TRADE_DATE)
    report = reconcile_session(_reader(symbols=("VIC",), include_vhm=False), version)

    assert report.status == "failed"
    assert report.capture_scope_matches_configuration is False


def test_reconciliation_rejects_an_interval_that_matches_a_future_trade_prefix() -> None:
    version = load_configuration(Path("t0-trading/config/trading.yaml")).resolve(TRADE_DATE)
    report = reconcile_session(
        _reader(interval_observed_time="2026/09/04 09:15:20"),
        version,
    )

    assert report.status == "failed"
    assert report.matched_interval_update_count == 1
    assert "interval_not_causal_trade_prefix" in report.differences[0]


def test_reconciliation_rejects_a_nonzero_full_session_volume_baseline() -> None:
    version = load_configuration(Path("t0-trading/config/trading.yaml")).resolve(TRADE_DATE)
    report = reconcile_session(_reader(first_total_volume=20), version)

    assert report.status == "failed"
    assert report.cumulative_volume_baseline_matches is False
    assert "VIC:first_trade_cumulative_volume_mismatch" in report.differences


def test_reconciliation_rejects_provider_interval_progression_rollback() -> None:
    rows = [
        _trade_row(
            1,
            symbol="VIC",
            trading_time="2026/09/04 09:15:05",
            price=100,
            quantity=10,
            side="B",
            total_volume=10,
            received_at=datetime(2026, 9, 4, 2, 15, 6, tzinfo=UTC),
        ),
        _interval_row(
            2,
            observed_time="2026/09/04 09:15:10",
            close=100,
            high=100,
            volume=10,
            received_at=datetime(2026, 9, 4, 2, 15, 11, tzinfo=UTC),
        ),
        _trade_row(
            3,
            symbol="VIC",
            trading_time="2026/09/04 09:15:40",
            price=102,
            quantity=20,
            side="S",
            total_volume=30,
            received_at=datetime(2026, 9, 4, 2, 15, 41, tzinfo=UTC),
        ),
        _interval_row(
            4,
            observed_time="2026/09/04 09:15:50",
            close=102,
            high=102,
            volume=30,
            received_at=datetime(2026, 9, 4, 2, 15, 51, tzinfo=UTC),
        ),
        _interval_row(
            5,
            observed_time="2026/09/04 09:15:55",
            close=100,
            high=100,
            volume=10,
            received_at=datetime(2026, 9, 4, 2, 15, 56, tzinfo=UTC),
        ),
        _trade_row(
            6,
            symbol="VHM",
            trading_time="2026/09/04 09:00:00",
            price=0,
            quantity=0,
            side="U",
            total_volume=0,
            received_at=datetime(2026, 9, 4, 2, 15, 57, tzinfo=UTC),
        ),
    ]
    version = load_configuration(Path("t0-trading/config/trading.yaml")).resolve(TRADE_DATE)
    report = reconcile_session(_reader_from_rows(rows), version)

    assert report.status == "failed"
    assert report.matched_interval_update_count == 3
    assert report.provider_interval_progression_issue_count == 1
    assert any("interval_volume_regression" in item for item in report.differences)


def test_reconciliation_rejects_executable_trades_outside_trading_sessions() -> None:
    rows = [
        _trade_row(
            1,
            symbol="VIC",
            trading_time="2026/09/04 09:15:05",
            price=100,
            quantity=10,
            side="B",
            total_volume=10,
            received_at=datetime(2026, 9, 4, 2, 15, 6, tzinfo=UTC),
        ),
        _interval_row(
            2,
            observed_time="2026/09/04 09:15:20",
            close=100,
            high=100,
            volume=10,
            received_at=datetime(2026, 9, 4, 2, 15, 21, tzinfo=UTC),
        ),
        _trade_row(
            3,
            symbol="VHM",
            trading_time="2026/09/04 09:00:00",
            price=0,
            quantity=0,
            side="U",
            total_volume=0,
            received_at=datetime(2026, 9, 4, 2, 15, 22, tzinfo=UTC),
        ),
        _trade_row(
            4,
            symbol="VIC",
            trading_time="2026/09/04 12:00:00",
            price=101,
            quantity=5,
            side="B",
            total_volume=15,
            received_at=datetime(2026, 9, 4, 5, 0, 1, tzinfo=UTC),
        ),
    ]
    version = load_configuration(Path("t0-trading/config/trading.yaml")).resolve(TRADE_DATE)
    report = reconcile_session(_reader_from_rows(rows), version)

    assert report.status == "failed"
    assert report.out_of_session_trade_count == 1
    assert "trade_outside_session[lunch_break]=1" in report.differences


def test_reader_rejects_an_oversized_batch_before_fetching_it() -> None:
    with pytest.raises(ValueError, match="less than or equal"):
        StreamBatch.model_validate(
            {
                "batch_id": "a" * 64,
                "first_receive_sequence": 1,
                "last_receive_sequence": MAX_STREAM_BATCH_MESSAGES + 1,
                "message_count": MAX_STREAM_BATCH_MESSAGES + 1,
                "object_key": "batch.json.gz",
                "object_sha256": "b" * 64,
                "published_at": "2026-09-04T02:00:00Z",
            }
        )


def test_reader_rejects_a_row_published_before_it_was_received() -> None:
    row = _trade_row(
        1,
        symbol="VIC",
        trading_time="2026/09/04 09:15:05",
        price=100,
        quantity=10,
        side="B",
        total_volume=10,
        received_at=datetime(2026, 9, 4, 2, 15, 6, tzinfo=UTC),
    )
    reader = _reader_from_rows(
        [row],
        symbols=("VIC",),
        batch_published_at="2026-09-04T02:00:00+00:00",
    )

    with pytest.raises(StreamCaptureReadError, match="row lineage"):
        tuple(reader.envelopes())


def test_reader_rejects_receipt_time_regression() -> None:
    rows = [
        _trade_row(
            1,
            symbol="VIC",
            trading_time="2026/09/04 09:15:05",
            price=100,
            quantity=10,
            side="B",
            total_volume=10,
            received_at=datetime(2026, 9, 4, 2, 15, 7, tzinfo=UTC),
        ),
        _trade_row(
            2,
            symbol="VIC",
            trading_time="2026/09/04 09:15:04",
            price=101,
            quantity=10,
            side="B",
            total_volume=20,
            received_at=datetime(2026, 9, 4, 2, 15, 6, tzinfo=UTC),
        ),
    ]

    with pytest.raises(StreamCaptureReadError, match="row lineage"):
        tuple(_reader_from_rows(rows, symbols=("VIC",)).envelopes())


def test_reader_rejects_batch_checksum_drift() -> None:
    with pytest.raises(StreamCaptureReadError, match="checksum drift"):
        tuple(_reader(corrupt=True).envelopes())
