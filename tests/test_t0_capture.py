import gzip
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError
from t0_trading.capture import (
    SSI_REST_RAW_PREFIX,
    CaptureOptions,
    S3CaptureStore,
    capture_rest,
)
from t0_trading.cli import app
from typer.testing import CliRunner


class _S3:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.puts = 0

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        try:
            body, metadata = self.objects[f"{Bucket}/{Key}"]
        except KeyError:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject") from None
        return {"ContentLength": len(body), "Metadata": metadata}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        body, _ = self.objects[f"{Bucket}/{Key}"]
        return {"Body": io.BytesIO(body)}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **values: Any) -> None:
        object_id = f"{Bucket}/{Key}"
        if object_id in self.objects and values.get("IfNoneMatch") == "*":
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[object_id] = (Body, values["Metadata"])
        self.puts += 1


@dataclass
class _Security:
    symbol: str
    board: str = "HOSE"
    lot_size: int = 100


@dataclass
class _Bar:
    symbol: str
    trading_date: str
    open_price: int = 100
    high_price: int = 101
    low_price: int = 99
    close_price: int = 100
    volume: int = 10
    value: int = 1000


@dataclass
class _Index:
    trading_date: str
    index_value: float = 1234.5


class _Market:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def get_securities_info(self, symbol: str) -> _Security:
        self.calls.append(("info", symbol))
        return _Security(symbol)

    def get_securities_summary_historical(
        self, symbol: str, from_date: str, to_date: str
    ) -> list[_Bar]:
        self.calls.append(("summary", symbol))
        assert from_date == to_date == "2026/08/26"
        return [_Bar(symbol, from_date)]

    def get_ohlc_1day_historical(
        self, symbol: str, from_date: str, to_date: str, *, page: int, size: int
    ) -> list[_Bar]:
        self.calls.append(("day", (symbol, page, size)))
        return [_Bar(symbol, from_date)] if page == 1 else []

    def get_ohlc_1minute_historical(
        self, symbol: str, from_date: str, to_date: str, *, page: int, size: int
    ) -> list[_Bar]:
        self.calls.append(("minute", (symbol, page, size)))
        return [_Bar(symbol, f"{from_date[:10]} 09:00:00")] if page == 1 else []

    def get_master_data_historical(self, from_date: str, to_date: str) -> list[_Security]:
        self.calls.append(("master", from_date))
        assert from_date == to_date == "2026/08/26"
        return [_Security("VIC"), _Security("VHM"), _Security("SSI")]

    def get_index_summary_historical(self, index: str, trading_date: str) -> _Index:
        self.calls.append(("index", index))
        return _Index(trading_date)


def test_rest_capture_is_immutable_scoped_and_idempotent() -> None:
    s3 = _S3()
    store = S3CaptureStore(s3, "s3://landing/root")
    market = _Market()
    instant = datetime(2026, 8, 27, tzinfo=UTC)

    def clock() -> datetime:
        nonlocal instant
        instant += timedelta(milliseconds=1)
        return instant

    options = CaptureOptions(
        trade_date=date(2026, 8, 26),
        job_token="manual__2026-08-27",
        symbols=("VIC", "VHM"),
        indices=("VNINDEX", "VN30"),
        page_size=2,
    )
    manifest_uri = capture_rest(market, store, options, clock=clock)
    initial_calls = list(market.calls)
    initial_puts = s3.puts

    assert capture_rest(market, store, options, clock=clock) == manifest_uri
    assert market.calls == initial_calls
    assert s3.puts == initial_puts
    assert manifest_uri.startswith(f"s3://landing/root/{SSI_REST_RAW_PREFIX}/")

    manifest_key = manifest_uri.removeprefix("s3://landing/")
    manifest = json.loads(s3.objects[f"landing/{manifest_key}"][0])
    assert manifest["trade_date"] == "2026-08-26"
    assert manifest["symbols"] == ["VIC", "VHM"]
    assert manifest["indices"] == ["VNINDEX", "VN30"]
    assert len(manifest["requests"]) == 11
    serialized = json.dumps(manifest)
    assert "manual__2026-08-27" not in serialized

    raw_objects = [
        (key, body, metadata)
        for key, (body, metadata) in s3.objects.items()
        if key.endswith(".json.gz")
    ]
    assert raw_objects
    for _key, body, metadata in raw_objects:
        assert hashlib.sha256(body).hexdigest() == metadata["sha256"]
        records = [json.loads(line) for line in gzip.decompress(body).splitlines()]
        assert all(
            record["record_sha256"] == hashlib.sha256(record["record_json"].encode()).hexdigest()
            for record in records
        )
        if "get_master_data_historical" in {record["endpoint"] for record in records}:
            assert {record["symbol"] for record in records} == {"VIC", "VHM"}


def test_rest_capture_rejects_an_existing_manifest_from_another_sdk() -> None:
    s3 = _S3()
    store = S3CaptureStore(s3, "s3://landing/root")
    options = CaptureOptions(
        trade_date=date(2026, 8, 26),
        job_token="manual__2026-08-27",
    )
    manifest_uri = capture_rest(_Market(), store, options)
    manifest_key = manifest_uri.removeprefix("s3://landing/")
    object_id = f"landing/{manifest_key}"
    manifest = json.loads(s3.objects[object_id][0])
    manifest["sdk_version"] = "3.2.0"
    body = json.dumps(
        manifest,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    s3.objects[object_id] = (body, {"sha256": hashlib.sha256(body).hexdigest()})

    try:
        capture_rest(_Market(), store, options)
    except RuntimeError as error:
        assert "does not match the requested scope" in str(error)
    else:
        raise AssertionError("Expected an old SDK capture manifest to be rejected")


def test_capture_options_reject_unbounded_pagination() -> None:
    try:
        CaptureOptions(
            trade_date=date(2026, 8, 26),
            job_token="run",
            page_size=1001,
        )
    except ValueError as error:
        assert "page_size" in str(error)
    else:
        raise AssertionError("Expected invalid page size to be rejected")


def test_capture_options_reject_noncanonical_scope() -> None:
    for symbols in (("vic",), ("VIC", "VIC"), ("",)):
        try:
            CaptureOptions(
                trade_date=date(2026, 8, 26),
                job_token="run",
                symbols=symbols,
            )
        except ValueError as error:
            assert "symbols" in str(error)
        else:
            raise AssertionError("Expected noncanonical symbols to be rejected")


def test_cli_help_and_trade_date_validation_do_not_initialize_aws(monkeypatch: Any) -> None:
    def unexpected_aws(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("validation must happen before AWS initialization")

    monkeypatch.setattr("t0_trading.cli.load_credentials", unexpected_aws)
    runner = CliRunner()
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    assert "capture-rest" in help_result.stdout

    invalid = runner.invoke(
        app,
        [
            "capture-rest",
            "--trade-date",
            "26-08-2026",
            "--job-token",
            "test-run",
            "--landing-uri",
            "s3://landing/root",
        ],
    )
    assert invalid.exit_code == 2
    assert "YYYY-MM-DD" in invalid.output

    current_date = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat()
    current = runner.invoke(
        app,
        [
            "capture-rest",
            "--trade-date",
            current_date,
            "--job-token",
            "test-run",
            "--landing-uri",
            "s3://landing/root",
        ],
    )
    assert current.exit_code == 2
    assert "current market" in current.output


def test_capture_cli_reports_safe_aws_failure_details(monkeypatch: Any) -> None:
    def denied(*_args: object, **_kwargs: object) -> None:
        raise ClientError(
            {
                "Error": {
                    "Code": "AccessDenied",
                    "Message": "sensitive provider detail",
                }
            },
            "HeadObject",
        )

    monkeypatch.setattr("t0_trading.cli.load_credentials", denied)
    result = CliRunner().invoke(
        app,
        [
            "capture-rest",
            "--trade-date",
            "2026-08-26",
            "--job-token",
            "test-run",
            "--landing-uri",
            "s3://landing",
        ],
    )

    assert result.exit_code == 1
    assert "ClientError operation=HeadObject code=AccessDenied" in result.output
    assert "sensitive provider detail" not in result.output
