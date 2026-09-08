from datetime import date, timedelta
from typing import Any

from botocore.exceptions import ClientError
from t0_trading.cli import app
from t0_trading.trading_dates import TradingDateError
from typer.testing import CliRunner


def test_cli_help_and_validation_do_not_initialize_aws(monkeypatch: Any) -> None:
    def unexpected_aws(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("validation must happen before AWS initialization")

    monkeypatch.setattr("t0_trading.cli.load_credentials", unexpected_aws)
    runner = CliRunner()
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    assert "capture-rest" in help_result.stdout
    assert "capture-stream" in help_result.stdout
    assert "audit-features" in help_result.stdout
    assert "certify-stream-day" in help_result.stdout
    assert "check-config" in help_result.stdout

    invalid_rest = runner.invoke(
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
    assert invalid_rest.exit_code == 2
    assert "YYYY-MM-DD" in invalid_rest.output

    invalid_stream = runner.invoke(
        app,
        [
            "capture-stream",
            "--landing-uri",
            "s3://landing/root",
            "--heartbeat-seconds",
            "30",
            "--stale-after-seconds",
            "20",
        ],
    )
    assert invalid_stream.exit_code == 2
    assert "must exceed heartbeat_seconds" in invalid_stream.output

    future = runner.invoke(
        app,
        [
            "capture-rest",
            "--trade-date",
            (date.today() + timedelta(days=2)).isoformat(),
            "--job-token",
            "test-run",
            "--landing-uri",
            "s3://landing/root",
        ],
    )
    assert future.exit_code == 2
    assert "current market" in future.output


def test_certify_stream_day_skips_an_unobserved_trading_date(monkeypatch: Any) -> None:
    class _Context:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *_args: object) -> None:
            return None

    class _Data:
        market_data = object()

        def __enter__(self) -> "_Data":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def unobserved(*_args: object, **_kwargs: object) -> None:
        raise TradingDateError("trade_date is not an SSI-observed trading day.")

    def credentials(*_args: object) -> object:
        return object()

    def authentication(*_args: object) -> _Context:
        return _Context()

    def data(*_args: object) -> _Data:
        return _Data()

    monkeypatch.setattr("t0_trading.cli.load_credentials", credentials)
    monkeypatch.setattr("t0_trading.cli.authenticated", authentication)
    monkeypatch.setattr("t0_trading.cli.Data", data)
    monkeypatch.setattr("t0_trading.cli.require_observed_trade_date", unobserved)

    result = CliRunner().invoke(
        app,
        [
            "certify-stream-day",
            "--trade-date",
            "2026-09-04",
            "--landing-uri",
            "s3://landing/root",
        ],
    )

    assert result.exit_code == 99
    assert "not an SSI-observed trading day" in result.output


def test_cli_reports_safe_aws_failure_details(monkeypatch: Any) -> None:
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


def test_feature_audit_reports_safe_aws_failure_details(monkeypatch: Any) -> None:
    def denied(*_args: object, **_kwargs: object) -> None:
        raise ClientError(
            {
                "Error": {
                    "Code": "AccessDenied",
                    "Message": "sensitive object detail",
                }
            },
            "GetObject",
        )

    def client(*_args: object, **_kwargs: object) -> object:
        return object()

    monkeypatch.setattr("t0_trading.cli.boto3.client", client)
    monkeypatch.setattr("t0_trading.cli.StreamSessionReader.from_uri", denied)
    result = CliRunner().invoke(
        app,
        [
            "audit-features",
            "--manifest-uri",
            "s3://landing/stream/manifest.json",
        ],
    )

    assert result.exit_code == 1
    assert "ClientError operation=GetObject code=AccessDenied" in result.output
    assert "sensitive object detail" not in result.output
