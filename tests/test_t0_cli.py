from datetime import date, timedelta
from typing import Any

from botocore.exceptions import ClientError
from t0_trading.cli import app
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
