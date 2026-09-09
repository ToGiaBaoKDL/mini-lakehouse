"""T0 capability command-line boundary."""

from __future__ import annotations

import json
import os
import signal
from datetime import date, datetime
from pathlib import Path
from threading import Event
from typing import Annotated
from zoneinfo import ZoneInfo

import boto3
import typer
from botocore.exceptions import ClientError
from pydantic import BaseModel
from ssi_sdk import Data, Stream

from t0_trading.capture import MAX_STREAM_BATCH_MESSAGES
from t0_trading.capture.reader import (
    StreamCaptureReadError,
    StreamSessionReader,
    stream_manifest_uris,
)
from t0_trading.capture.rest import RestCaptureOptions, capture_rest
from t0_trading.capture.spool import CaptureSpool
from t0_trading.capture.store import CaptureStoreUnavailable, S3CaptureStore
from t0_trading.capture.stream import StreamCaptureOptions, capture_stream
from t0_trading.certification import CertificationOptions, run_certification
from t0_trading.configuration import (
    TradingConfiguration,
    TradingConfigurationError,
    load_configuration,
)
from t0_trading.credentials import CredentialError, load_credentials
from t0_trading.features import (
    FeatureAuditReport,
    FeatureSnapshot,
    build_feature_audit,
    replay_features,
)
from t0_trading.market.reconciliation import (
    ReconciliationReport,
    reconcile_session,
    reconcile_trade_date,
)
from t0_trading.outcomes import build_outcome_audit, label_outcomes
from t0_trading.provider import authenticated
from t0_trading.trading_dates import TradingDateError, require_observed_trade_date

MARKET_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")
DEFAULT_TRADING_CONFIG = Path("t0-trading/config/trading.yaml")


def _safe_error(error: Exception) -> str:
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code", "Unknown")
        return f"ClientError operation={error.operation_name} code={code}"
    return type(error).__name__


def _values(value: str, label: str) -> tuple[str, ...]:
    items = tuple(dict.fromkeys(item.strip().upper() for item in value.split(",") if item.strip()))
    if not items:
        raise typer.BadParameter(f"{label} cannot be empty.")
    return items


def _parse_trade_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise typer.BadParameter(
            "must use YYYY-MM-DD format.", param_hint="--trade-date"
        ) from error
    if parsed > datetime.now(MARKET_TIMEZONE).date():
        raise typer.BadParameter(
            "must not be later than the current market date.", param_hint="--trade-date"
        )
    return parsed


def _emit_model(report: BaseModel, output: Path | None = None) -> None:
    rendered = report.model_dump_json(indent=2)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"{rendered}\n", encoding="utf-8")
    typer.echo(rendered)


def _emit_reconciliation(report: ReconciliationReport, output: Path | None = None) -> None:
    _emit_model(report, output)
    if report.status != "passed":
        raise typer.Exit(code=1)


def _replay_feature_session(
    manifest_uri: str,
    region: str,
    config: Path,
) -> tuple[
    StreamSessionReader,
    TradingConfiguration,
    tuple[FeatureSnapshot, ...],
    FeatureAuditReport,
]:
    """Read, replay, and validate one complete feature session without retaining raw input."""
    reader = StreamSessionReader.from_uri(
        boto3.client("s3", region_name=region),
        manifest_uri,
    )
    configuration = load_configuration(config)
    version = configuration.resolve(reader.trade_date)
    snapshots = replay_features(reader.envelopes(), version, trade_date=reader.trade_date)
    report = build_feature_audit(
        snapshots,
        version,
        trade_date=reader.trade_date,
        manifest_uri=reader.uri,
        stream_session_id=reader.manifest.stream_session_id,
        input_message_count=reader.manifest.message_count,
    )
    return reader, configuration, snapshots, report


def check_config(
    config: Annotated[Path, typer.Option(help="Versioned non-secret trading YAML.")] = (
        DEFAULT_TRADING_CONFIG
    ),
    effective_date: Annotated[
        str | None,
        typer.Option(help="Configuration date in YYYY-MM-DD; defaults to the local market date."),
    ] = None,
) -> None:
    """Validate trading configuration and print only its immutable identity."""
    try:
        selected_date = (
            date.fromisoformat(effective_date)
            if effective_date is not None
            else datetime.now(MARKET_TIMEZONE).date()
        )
    except ValueError as error:
        raise typer.BadParameter(
            "must use YYYY-MM-DD format.", param_hint="--effective-date"
        ) from error
    try:
        configuration = load_configuration(config)
        version = configuration.resolve(selected_date)
        outcomes = configuration.resolve_outcomes(selected_date)
    except TradingConfigurationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        json.dumps(
            {
                "configuration_sha256": configuration.sha256,
                "effective_date": selected_date.isoformat(),
                "outcome_version": outcomes.version,
                "outcome_version_sha256": outcomes.sha256,
                "version": version.version,
                "version_sha256": version.sha256,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


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
    except Exception as error:  # SDK boundary: never print provider request state.
        typer.echo(f"SSI certification failed: {_safe_error(error)}", err=True)
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
    config: Annotated[Path, typer.Option(help="Versioned non-secret trading YAML.")] = (
        DEFAULT_TRADING_CONFIG
    ),
    page_size: Annotated[int, typer.Option(min=1, max=1000)] = 1000,
) -> None:
    """Capture one bounded SSI REST trade date as immutable S3 evidence."""
    parsed_trade_date = _parse_trade_date(trade_date)
    environment = os.environ.get("LAKEHOUSE_ENVIRONMENT", "dev")
    effective_secret_id = secret_id or f"lakehouse/{environment}/t0-trading/ssi"
    try:
        # Capture scope follows the deployed configuration. The requested trade date is
        # source lineage, not a request to apply historical strategy policy.
        version = load_configuration(config).resolve(datetime.now(MARKET_TIMEZONE).date())
        credentials = load_credentials(effective_secret_id, region)
        store = S3CaptureStore(boto3.client("s3", region_name=region), landing_uri)
        with authenticated(credentials) as auth, Data(auth) as data:
            require_observed_trade_date(
                data.market_data,
                trade_date=parsed_trade_date,
            )
            manifest_uri = capture_rest(
                data.market_data,
                store,
                RestCaptureOptions(
                    trade_date=parsed_trade_date,
                    job_token=job_token,
                    symbols=version.market.symbols,
                    indices=version.market.indices,
                    page_size=page_size,
                ),
            )
    except (CredentialError, TradingConfigurationError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except TradingDateError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=99) from error
    except Exception as error:  # SDK/AWS boundary: never print provider payload or credentials.
        typer.echo(f"SSI REST capture failed: {_safe_error(error)}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(manifest_uri)


def capture_stream_command(
    landing_uri: Annotated[str, typer.Option(help="Landing S3 root URI.")],
    secret_id: Annotated[
        str | None,
        typer.Option(help="Managed SSI market-data secret; defaults from the environment."),
    ] = None,
    region: Annotated[str, typer.Option(help="AWS region for the secret and landing bucket.")] = (
        "ap-southeast-1"
    ),
    config: Annotated[Path, typer.Option(help="Versioned non-secret trading YAML.")] = (
        DEFAULT_TRADING_CONFIG
    ),
    duration_seconds: Annotated[float, typer.Option(min=1, max=86_400)] = 600,
    heartbeat_seconds: Annotated[float, typer.Option(min=5, max=300)] = 30,
    stale_after_seconds: Annotated[float, typer.Option(min=10, max=900)] = 90,
    flush_seconds: Annotated[float, typer.Option(min=1, max=60)] = 30,
    batch_size: Annotated[int, typer.Option(min=1, max=MAX_STREAM_BATCH_MESSAGES)] = 500,
    spool_dir: Annotated[
        Path | None,
        typer.Option(help="Optional persistent directory for pending stream objects."),
    ] = None,
    spool_max_bytes: Annotated[int, typer.Option(min=1)] = 268_435_456,
    ready_file: Annotated[
        Path | None,
        typer.Option(help="Optional runtime readiness marker written after the first heartbeat."),
    ] = None,
) -> None:
    """Capture one bounded SSI stream session as immutable S3 micro-batches."""
    environment = os.environ.get("LAKEHOUSE_ENVIRONMENT", "dev")
    effective_secret_id = secret_id or f"lakehouse/{environment}/t0-trading/ssi"
    try:
        version = load_configuration(config).resolve(datetime.now(MARKET_TIMEZONE).date())
        options = StreamCaptureOptions(
            symbols=version.market.symbols,
            duration_seconds=duration_seconds,
            heartbeat_seconds=heartbeat_seconds,
            stale_after_seconds=stale_after_seconds,
            flush_seconds=flush_seconds,
            batch_size=batch_size,
        )
    except TradingConfigurationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    if ready_file is not None:
        ready_file.unlink(missing_ok=True)
    stop = Event()
    previous_handlers = {
        signum: signal.signal(signum, lambda _signum, _frame: stop.set())
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        credentials = load_credentials(effective_secret_id, region)
        store = S3CaptureStore(boto3.client("s3", region_name=region), landing_uri)
        spool = CaptureSpool(spool_dir, max_bytes=spool_max_bytes) if spool_dir else None
        with authenticated(credentials) as auth, Stream(auth) as stream:
            manifest_uri = capture_stream(
                stream.streaming,
                store,
                options,
                stop=stop,
                on_ready=(lambda: ready_file.touch()) if ready_file is not None else None,
                spool=spool,
            )
    except CredentialError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except Exception as error:  # SDK/AWS boundary: never print provider payload or credentials.
        typer.echo(f"SSI stream capture failed: {_safe_error(error)}", err=True)
        raise typer.Exit(code=1) from None
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    typer.echo(manifest_uri)


def reconcile_stream_command(
    manifest_uri: Annotated[
        str,
        typer.Option(help="Terminal SSI Stream manifest S3 URI."),
    ],
    region: Annotated[str, typer.Option(help="AWS region containing the landing bucket.")] = (
        "ap-southeast-1"
    ),
    config: Annotated[Path, typer.Option(help="Versioned non-secret trading YAML.")] = (
        DEFAULT_TRADING_CONFIG
    ),
    output: Annotated[
        Path | None,
        typer.Option(help="Optional path for the JSON report; stdout is always emitted."),
    ] = None,
) -> None:
    """Verify and replay one full SSI stream session, then reconcile its minute bars."""
    try:
        reader = StreamSessionReader.from_uri(
            boto3.client("s3", region_name=region),
            manifest_uri,
        )
        version = load_configuration(config).resolve(reader.trade_date)
        report = reconcile_session(reader, version)
    except (StreamCaptureReadError, TradingConfigurationError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except (CaptureStoreUnavailable, ClientError) as error:
        typer.echo(f"SSI stream reconciliation failed: {_safe_error(error)}", err=True)
        raise typer.Exit(code=1) from None
    _emit_reconciliation(report, output)


def audit_features_command(
    manifest_uri: Annotated[
        str,
        typer.Option(help="Terminal SSI Stream manifest S3 URI."),
    ],
    region: Annotated[str, typer.Option(help="AWS region containing the landing bucket.")] = (
        "ap-southeast-1"
    ),
    config: Annotated[Path, typer.Option(help="Versioned non-secret trading YAML.")] = (
        DEFAULT_TRADING_CONFIG
    ),
    output: Annotated[
        Path | None,
        typer.Option(help="Optional local JSON path; stdout is always emitted."),
    ] = None,
) -> None:
    """Replay and summarize one terminal session without publishing feature data."""
    try:
        _, _, _, report = _replay_feature_session(manifest_uri, region, config)
    except (StreamCaptureReadError, TradingConfigurationError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except (CaptureStoreUnavailable, ClientError) as error:
        typer.echo(f"SSI feature audit failed: {_safe_error(error)}", err=True)
        raise typer.Exit(code=1) from None
    _emit_model(report, output)


def audit_outcomes_command(
    manifest_uri: Annotated[
        str,
        typer.Option(help="Terminal SSI Stream manifest S3 URI."),
    ],
    region: Annotated[str, typer.Option(help="AWS region containing the landing bucket.")] = (
        "ap-southeast-1"
    ),
    config: Annotated[Path, typer.Option(help="Versioned non-secret trading YAML.")] = (
        DEFAULT_TRADING_CONFIG
    ),
    output: Annotated[
        Path | None,
        typer.Option(help="Optional local JSON path; stdout is always emitted."),
    ] = None,
) -> None:
    """Replay, label, and summarize one terminal session without publishing outcomes."""
    try:
        reader, configuration, snapshots, _ = _replay_feature_session(manifest_uri, region, config)
        version = configuration.resolve(reader.trade_date)
        policy = configuration.resolve_outcomes(reader.trade_date)
        labels = label_outcomes(snapshots, reader.envelopes(), version, policy)
        report = build_outcome_audit(
            snapshots,
            labels,
            version,
            policy,
            trade_date=reader.trade_date,
            manifest_uri=reader.uri,
            stream_session_id=reader.manifest.stream_session_id,
            input_message_count=reader.manifest.message_count,
        )
    except (StreamCaptureReadError, TradingConfigurationError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except (CaptureStoreUnavailable, ClientError) as error:
        typer.echo(f"SSI outcome audit failed: {_safe_error(error)}", err=True)
        raise typer.Exit(code=1) from None
    _emit_model(report, output)


def certify_stream_day_command(
    trade_date: Annotated[
        str,
        typer.Option(help="Exchange-local trade date in YYYY-MM-DD format."),
    ],
    landing_uri: Annotated[str, typer.Option(help="Landing S3 root URI.")],
    secret_id: Annotated[
        str | None,
        typer.Option(help="Managed SSI market-data secret; defaults from the environment."),
    ] = None,
    region: Annotated[str, typer.Option(help="AWS region for SSI credentials and S3.")] = (
        "ap-southeast-1"
    ),
    config: Annotated[Path, typer.Option(help="Versioned non-secret trading YAML.")] = (
        DEFAULT_TRADING_CONFIG
    ),
) -> None:
    """Certify one scheduled SSI Stream trade date before lakehouse publication."""
    parsed_trade_date = _parse_trade_date(trade_date)
    environment = os.environ.get("LAKEHOUSE_ENVIRONMENT", "dev")
    effective_secret_id = secret_id or f"lakehouse/{environment}/t0-trading/ssi"
    try:
        version = load_configuration(config).resolve(parsed_trade_date)
        credentials = load_credentials(effective_secret_id, region)
        with authenticated(credentials) as auth, Data(auth) as data:
            require_observed_trade_date(data.market_data, trade_date=parsed_trade_date)
        client = boto3.client("s3", region_name=region)
        readers = tuple(
            StreamSessionReader.from_uri(client, uri)
            for uri in stream_manifest_uris(client, landing_uri, parsed_trade_date)
        )
        report = reconcile_trade_date(readers, version, trade_date=parsed_trade_date)
    except TradingDateError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=99) from error
    except (
        CredentialError,
        StreamCaptureReadError,
        TradingConfigurationError,
        ValueError,
    ) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except Exception as error:  # SDK/AWS boundary: never print provider payload or credentials.
        typer.echo(f"SSI stream certification failed: {_safe_error(error)}", err=True)
        raise typer.Exit(code=1) from None
    _emit_reconciliation(report)


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


@app.callback()
def main() -> None:
    """Operate the evidence-gated T0 capability."""


app.command("certify")(certify)
app.command("check-config")(check_config)
app.command("capture-rest")(capture_rest_command)
app.command("capture-stream")(capture_stream_command)
app.command("reconcile-stream")(reconcile_stream_command)
app.command("audit-features")(audit_features_command)
app.command("audit-outcomes")(audit_outcomes_command)
app.command("certify-stream-day")(certify_stream_day_command)
