"""T0 capability command-line boundary."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Annotated

import boto3
import typer
from ssi_sdk import Data

from t0_trading.capture import CaptureOptions, S3CaptureStore, capture_rest
from t0_trading.certification import CertificationOptions, run_certification
from t0_trading.credentials import CredentialError, load_credentials
from t0_trading.provider import authenticated


def _values(value: str, label: str) -> tuple[str, ...]:
    items = tuple(dict.fromkeys(item.strip().upper() for item in value.split(",") if item.strip()))
    if not items:
        raise typer.BadParameter(f"{label} cannot be empty.")
    return items


def certify(
    output: Annotated[
        Path,
        typer.Option(help="Path for the sanitized JSON certification report."),
    ] = Path("/tmp/mini-lakehouse-t0-certification.json"),
    secret_id: Annotated[
        str,
        typer.Option(help="AWS Secrets Manager ID containing the v1 market-data credential."),
    ] = "lakehouse/dev/t0-trading/ssi",
    region: Annotated[str, typer.Option(help="AWS region containing the managed secret.")] = (
        "ap-southeast-1"
    ),
    symbols: Annotated[str, typer.Option(help="Comma-separated stock symbols.")] = "VIC,VHM",
    indices: Annotated[str, typer.Option(help="Comma-separated market indices.")] = "VNINDEX,VN30",
    history_days: Annotated[int, typer.Option(min=1, max=366)] = 10,
    page_size: Annotated[int, typer.Option(min=1, max=1000)] = 5,
    stream_seconds: Annotated[float, typer.Option(min=0, max=1800)] = 15,
    stream_cycles: Annotated[int, typer.Option(min=0, max=10)] = 1,
) -> None:
    """Capture sanitized evidence from SSI Data REST and Stream DATA."""
    try:
        credentials = load_credentials(secret_id, region)
        report = run_certification(
            credentials,
            CertificationOptions(
                symbols=_values(symbols, "symbols"),
                indices=_values(indices, "indices"),
                history_days=history_days,
                page_size=page_size,
                stream_seconds=stream_seconds,
                stream_cycles=stream_cycles,
            ),
        )
    except CredentialError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except Exception as error:  # SDK boundary: print only the type, never provider request state.
        typer.echo(f"SSI certification failed: {type(error).__name__}", err=True)
        raise typer.Exit(code=1) from None

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    typer.echo(
        json.dumps(
            {"output": str(output), **report["result"]},
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    if report["result"]["status"] != "passed":
        raise typer.Exit(code=1)


def capture_rest_command(
    trade_date: Annotated[
        str,
        typer.Option(help="Completed exchange-local trade date in YYYY-MM-DD format."),
    ],
    job_token: Annotated[
        str,
        typer.Option(help="Stable orchestration-run token used for idempotent capture."),
    ],
    landing_uri: Annotated[str, typer.Option(help="Landing S3 root URI.")],
    secret_id: Annotated[
        str | None,
        typer.Option(help="Managed SSI market-data secret; defaults from the environment."),
    ] = None,
    region: Annotated[str, typer.Option(help="AWS region for the secret and landing bucket.")] = (
        "ap-southeast-1"
    ),
    symbols: Annotated[str, typer.Option(help="Comma-separated stock symbols.")] = "VIC,VHM",
    indices: Annotated[str, typer.Option(help="Comma-separated market indices.")] = "VNINDEX,VN30",
    page_size: Annotated[int, typer.Option(min=1, max=1000)] = 1000,
) -> None:
    """Capture one bounded SSI REST trade date as immutable S3 evidence."""
    try:
        parsed_trade_date = date.fromisoformat(trade_date)
    except ValueError as error:
        raise typer.BadParameter(
            "must use YYYY-MM-DD format.", param_hint="--trade-date"
        ) from error
    environment = os.environ.get("LAKEHOUSE_ENVIRONMENT", "dev")
    effective_secret_id = secret_id or f"lakehouse/{environment}/t0-trading/ssi"
    try:
        credentials = load_credentials(effective_secret_id, region)
        store = S3CaptureStore(boto3.client("s3", region_name=region), landing_uri)
        with authenticated(credentials) as auth, Data(auth) as data:
            manifest_uri = capture_rest(
                data.market_data,
                store,
                CaptureOptions(
                    trade_date=parsed_trade_date,
                    job_token=job_token,
                    symbols=_values(symbols, "symbols"),
                    indices=_values(indices, "indices"),
                    page_size=page_size,
                ),
            )
    except CredentialError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except Exception as error:  # SDK/AWS boundary: never print provider payload or credentials.
        typer.echo(f"SSI REST capture failed: {type(error).__name__}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(manifest_uri)


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


@app.callback()
def main() -> None:
    """Operate the evidence-gated T0 capability."""


app.command("certify")(certify)
app.command("capture-rest")(capture_rest_command)
