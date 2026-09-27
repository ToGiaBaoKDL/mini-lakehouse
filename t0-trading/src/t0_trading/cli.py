"""T0 capability command-line boundary."""

from __future__ import annotations

import json
import os
import signal
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Event
from typing import Annotated
from zoneinfo import ZoneInfo

import boto3
import typer
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, ValidationError
from ssi_sdk import Data

from t0_trading.arbitration import (
    ShadowArbitrationAuditError,
    ShadowArbitrationJournal,
    audit_shadow_journal,
    prune_shadow_journals,
)
from t0_trading.capture import MAX_STREAM_BATCH_MESSAGES
from t0_trading.capture.reader import (
    StreamCaptureReadError,
    StreamDayReader,
    StreamSessionReader,
    stream_manifest_uris,
)
from t0_trading.capture.rest import RestCaptureOptions, capture_rest
from t0_trading.capture.spool import CaptureSpool
from t0_trading.capture.store import CaptureStoreUnavailable, S3CaptureStore
from t0_trading.capture.stream import StreamCaptureOptions, capture_stream_resilient
from t0_trading.certification import CertificationOptions, run_certification
from t0_trading.configuration import (
    EvaluationTier,
    TradingConfiguration,
    TradingConfigurationError,
    load_configuration,
)
from t0_trading.context import build_decision_contexts
from t0_trading.credentials import CredentialError, load_credentials
from t0_trading.features import (
    FeatureAuditReport,
    FeatureSnapshot,
    build_feature_audit,
    replay_features,
)
from t0_trading.market.reconciliation import (
    MarketDayCertification,
    ReconciliationReport,
    certify_market_day,
    reconcile_session,
    select_feature_capture,
)
from t0_trading.outcomes import OutcomeLabel, build_outcome_audit, label_outcomes
from t0_trading.provider import authenticated, market_stream
from t0_trading.simulation import SimulationRequest, simulate_cycles
from t0_trading.simulation.presets import (
    PUBLIC_VNDIRECT_DTA_CHECKED_AT,
    public_vndirect_dta_costs,
)
from t0_trading.strategy.baseline_audit import (
    BaselineAuditReport,
    evaluate_buy_first_baselines,
)
from t0_trading.strategy.baseline_walk_forward import (
    BaselineWalkForwardReport,
    evaluate_baseline_walk_forward,
)
from t0_trading.strategy.baselines import score_buy_first_baselines
from t0_trading.trading_dates import TradingDateError, require_observed_trade_date

MARKET_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")
DEFAULT_TRADING_CONFIG = Path("t0-trading/config/trading.yaml")
INELIGIBLE_EXIT_CODE = 10


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


def _emit_market_day_certification(certification: MarketDayCertification) -> None:
    _emit_model(certification)
    if certification.status != "passed":
        raise typer.Exit(code=INELIGIBLE_EXIT_CODE)


def _replay_feature_day(
    trade_date: date,
    landing_uri: str,
    region: str,
    config: Path,
) -> tuple[
    StreamDayReader,
    TradingConfiguration,
    tuple[FeatureSnapshot, ...],
    FeatureAuditReport,
]:
    """Certify and replay one logical market day without retaining raw input."""
    configuration = load_configuration(config)
    version = configuration.resolve(trade_date)
    readers = _stream_day_readers(trade_date, landing_uri, region)
    certification, _ = certify_market_day(readers, version, trade_date=trade_date)
    reader = select_feature_capture(readers, certification)
    snapshots, report = _replay_feature_capture(reader, configuration)
    return reader, configuration, snapshots, report


def _replay_feature_capture(
    reader: StreamDayReader,
    configuration: TradingConfiguration,
) -> tuple[tuple[FeatureSnapshot, ...], FeatureAuditReport]:
    version = configuration.resolve(reader.trade_date)
    snapshots = replay_features(
        reader.envelopes(),
        version,
        trade_date=reader.trade_date,
        gaps=reader.gaps,
    )
    report = build_feature_audit(snapshots, version, capture=reader)
    return snapshots, report


def _replay_outcome_day(
    trade_date: date,
    landing_uri: str,
    region: str,
    config: Path,
) -> tuple[
    StreamDayReader,
    TradingConfiguration,
    tuple[FeatureSnapshot, ...],
    tuple[OutcomeLabel, ...],
]:
    reader, configuration, snapshots, _ = _replay_feature_day(
        trade_date, landing_uri, region, config
    )
    version = configuration.resolve(reader.trade_date)
    policy = configuration.resolve_outcomes(reader.trade_date)
    labels = label_outcomes(
        snapshots,
        reader.envelopes(),
        version,
        policy,
        authorized_stream_session_ids=reader.stream_session_ids,
    )
    return reader, configuration, snapshots, labels


def _stream_day_readers(
    trade_date: date,
    landing_uri: str,
    region: str,
) -> tuple[StreamSessionReader, ...]:
    client = boto3.client("s3", region_name=region)
    manifest_uris = stream_manifest_uris(client, landing_uri, trade_date)
    if not manifest_uris:
        raise ValueError("no terminal SSI Stream session exists for the trading date")
    return tuple(StreamSessionReader.from_uri(client, uri) for uri in manifest_uris)


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
        capture_scope = configuration.capture_scope(selected_date)
        outcomes = configuration.resolve_outcomes(selected_date)
        exploratory_evaluation = configuration.resolve_baseline_evaluation(
            selected_date, "EXPLORATORY"
        )
        promotion_evaluation = configuration.resolve_baseline_evaluation(selected_date, "PROMOTION")
        context = configuration.resolve_context(selected_date)
        arbitration = configuration.resolve_candidate_arbitration(selected_date)
    except TradingConfigurationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        json.dumps(
            {
                "configuration_sha256": configuration.sha256,
                "capture_indices": capture_scope.indices,
                "capture_symbols": capture_scope.symbols,
                "effective_date": selected_date.isoformat(),
                "outcome_version": outcomes.version,
                "outcome_version_sha256": outcomes.sha256,
                "promotion_evaluation_version": promotion_evaluation.version,
                "promotion_evaluation_version_sha256": promotion_evaluation.sha256,
                "exploratory_evaluation_version": exploratory_evaluation.version,
                "exploratory_evaluation_version_sha256": exploratory_evaluation.sha256,
                "context_version": context.version,
                "context_version_sha256": context.sha256,
                "arbitration_version": arbitration.version if arbitration else None,
                "arbitration_version_sha256": arbitration.sha256 if arbitration else None,
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
    indices: Annotated[str, typer.Option(help="Comma-separated market indices.")] = (
        "VNINDEX,VN30,VNREAL"
    ),
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
        # Capture is an operational evidence concern. The requested trade date is source
        # lineage, while the deployed scope must still cover today's decision universe.
        configuration = load_configuration(config)
        capture_scope = configuration.capture_scope(datetime.now(MARKET_TIMEZONE).date())
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
                    symbols=capture_scope.symbols,
                    indices=capture_scope.indices,
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
    shadow_journal_dir: Annotated[
        Path | None,
        typer.Option(
            help="Optional persistent directory for local candidate arbitration journals."
        ),
    ] = None,
    ready_file: Annotated[
        Path | None,
        typer.Option(help="Optional runtime readiness marker written after the first heartbeat."),
    ] = None,
) -> None:
    """Capture one bounded market window as reconnect-safe immutable segments."""
    environment = os.environ.get("LAKEHOUSE_ENVIRONMENT", "dev")
    effective_secret_id = secret_id or f"lakehouse/{environment}/t0-trading/ssi"
    try:
        trade_date = datetime.now(MARKET_TIMEZONE).date()
        configuration = load_configuration(config)
        version = configuration.resolve(trade_date)
        capture_scope = configuration.capture_scope(trade_date)
        options = StreamCaptureOptions(
            symbols=capture_scope.symbols,
            indices=capture_scope.indices,
            markets=capture_scope.markets,
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
    journal: ShadowArbitrationJournal | None = None
    capture_completed = False
    try:
        credentials = load_credentials(effective_secret_id, region)
        store = S3CaptureStore(boto3.client("s3", region_name=region), landing_uri)
        spool = CaptureSpool(spool_dir, max_bytes=spool_max_bytes) if spool_dir else None
        if shadow_journal_dir is not None:
            try:
                started_at = datetime.now(UTC)
                prune_shadow_journals(shadow_journal_dir, observed_at=started_at)
                arbitration = configuration.resolve_candidate_arbitration(trade_date)
                if arbitration is not None:
                    output = shadow_journal_dir / (
                        f"{trade_date.isoformat()}T{started_at.strftime('%H%M%S.%fZ')}"
                        ".arbitrations.jsonl"
                    )
                    journal = ShadowArbitrationJournal(
                        output,
                        trade_date,
                        version,
                        configuration.resolve_context(trade_date),
                        arbitration,
                        on_error=lambda error: typer.echo(
                            f"T0 shadow journal disabled ({type(error).__name__})",
                            err=True,
                        ),
                    )
            except (OSError, ValueError) as error:
                typer.echo(
                    f"T0 shadow journal unavailable ({type(error).__name__})",
                    err=True,
                )
        manifest_uris = capture_stream_resilient(
            lambda: market_stream(credentials),
            store,
            options,
            stop=stop,
            on_ready=(lambda: ready_file.touch()) if ready_file is not None else None,
            on_unavailable=(
                lambda: ready_file.unlink(missing_ok=True) if ready_file is not None else None
            ),
            on_retry=lambda error, delay: typer.echo(
                f"SSI stream unavailable ({type(error).__name__}); reconnecting in {delay:g}s",
                err=True,
            ),
            spool=spool,
            observer=journal,
        )
        if journal is not None:
            journal.close(datetime.now(UTC), manifest_uris)
        capture_completed = True
    except CredentialError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except Exception as error:  # SDK/AWS boundary: never print provider payload or credentials.
        typer.echo(f"SSI stream capture failed: {_safe_error(error)}", err=True)
        raise typer.Exit(code=1) from None
    finally:
        if journal is not None and not capture_completed:
            journal.abort()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    if journal is not None and journal.arbitration_sha256 is not None:
        typer.echo(
            json.dumps(
                {
                    "arbitration_count": journal.arbitration_count,
                    "arbitration_sha256": journal.arbitration_sha256,
                    "candidate_count": journal.candidate_count,
                    "manifest": str(journal.manifest_output),
                    "manifest_sha256": journal.manifest.sha256 if journal.manifest else None,
                    "output": str(journal.output),
                    "trade_date": trade_date.isoformat(),
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            err=True,
        )
    for manifest_uri in manifest_uris:
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
    trade_date: Annotated[
        str,
        typer.Option(help="Certified exchange-local trade date in YYYY-MM-DD format."),
    ],
    landing_uri: Annotated[str, typer.Option(help="Landing S3 root URI.")],
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
    """Replay and summarize one certified market day without publishing feature data."""
    parsed_trade_date = _parse_trade_date(trade_date)
    try:
        _, _, _, report = _replay_feature_day(parsed_trade_date, landing_uri, region, config)
    except (StreamCaptureReadError, TradingConfigurationError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except (CaptureStoreUnavailable, ClientError) as error:
        typer.echo(f"SSI feature audit failed: {_safe_error(error)}", err=True)
        raise typer.Exit(code=1) from None
    _emit_model(report, output)


def audit_outcomes_command(
    trade_date: Annotated[
        str,
        typer.Option(help="Certified exchange-local trade date in YYYY-MM-DD format."),
    ],
    landing_uri: Annotated[str, typer.Option(help="Landing S3 root URI.")],
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
    """Replay, label, and summarize one certified day without publishing outcomes."""
    parsed_trade_date = _parse_trade_date(trade_date)
    try:
        reader, configuration, snapshots, labels = _replay_outcome_day(
            parsed_trade_date, landing_uri, region, config
        )
        version = configuration.resolve(reader.trade_date)
        policy = configuration.resolve_outcomes(reader.trade_date)
        report = build_outcome_audit(
            snapshots,
            labels,
            version,
            policy,
            capture=reader,
        )
    except (StreamCaptureReadError, TradingConfigurationError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except (CaptureStoreUnavailable, ClientError) as error:
        typer.echo(f"SSI outcome audit failed: {_safe_error(error)}", err=True)
        raise typer.Exit(code=1) from None
    _emit_model(report, output)


def audit_buy_first_baselines_command(
    trade_date: Annotated[
        str,
        typer.Option(help="Certified exchange-local trade date in YYYY-MM-DD format."),
    ],
    landing_uri: Annotated[str, typer.Option(help="Landing S3 root URI.")],
    region: Annotated[str, typer.Option(help="AWS region containing the landing bucket.")] = (
        "ap-southeast-1"
    ),
    config: Annotated[Path, typer.Option(help="Versioned non-secret trading YAML.")] = (
        DEFAULT_TRADING_CONFIG
    ),
    output: Annotated[
        Path | None,
        typer.Option(help="Optional local JSON report path; stdout is always emitted."),
    ] = None,
) -> None:
    """Audit three buy-first brief baselines; never emit a live signal."""
    parsed_trade_date = _parse_trade_date(trade_date)
    try:
        reader, configuration, snapshots, labels = _replay_outcome_day(
            parsed_trade_date, landing_uri, region, config
        )
        contexts = build_decision_contexts(
            snapshots,
            reader.envelopes(),
            configuration.resolve(parsed_trade_date),
            configuration.resolve_context(parsed_trade_date),
        )
        candidates = score_buy_first_baselines(snapshots, contexts)
        costs = public_vndirect_dta_costs(
            reader.trade_date,
            checked_at=PUBLIC_VNDIRECT_DTA_CHECKED_AT,
        )
        report = evaluate_buy_first_baselines(
            candidates,
            labels,
            costs,
            capture_evidence_sha256=reader.evidence_sha256,
        )
    except (StreamCaptureReadError, TradingConfigurationError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except (CaptureStoreUnavailable, BotoCoreError) as error:
        typer.echo(f"SSI buy-first baseline audit failed: {_safe_error(error)}", err=True)
        raise typer.Exit(code=1) from None
    _emit_model(report, output)


def audit_baseline_walk_forward_command(
    report_file: Annotated[
        list[Path],
        typer.Option(help="Daily BaselineAuditReport JSON; repeat in any date order."),
    ],
    config: Annotated[Path, typer.Option(help="Versioned non-secret trading YAML.")] = (
        DEFAULT_TRADING_CONFIG
    ),
    output: Annotated[
        Path | None,
        typer.Option(help="Optional local JSON path; stdout is always emitted."),
    ] = None,
    evaluation_tier: Annotated[
        EvaluationTier,
        typer.Option(help="EXPLORATORY shadow gate or PROMOTION evidence gate."),
    ] = "EXPLORATORY",
) -> None:
    """Evaluate fixed baseline formulas on purged, untouched future sessions."""
    try:
        configuration = load_configuration(config)
        sessions: dict[date, BaselineAuditReport] = {}
        for path in report_file:
            report = BaselineAuditReport.model_validate_json(path.read_bytes())
            if report.trade_date in sessions:
                raise ValueError("baseline audit report dates must be unique")
            sessions[report.trade_date] = report
        if not sessions:
            raise ValueError("baseline walk-forward requires daily audit reports")
        first_date = min(sessions)
        result: BaselineWalkForwardReport = evaluate_baseline_walk_forward(
            sessions,
            configuration.resolve_baseline_evaluation(first_date, evaluation_tier),
        )
    except (OSError, TradingConfigurationError, ValidationError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    _emit_model(result, output)


def simulate_cycles_command(
    input_file: Annotated[
        Path,
        typer.Option("--input", help="Local JSON with account, costs, marks, and selected cycles."),
    ],
    output: Annotated[
        Path | None,
        typer.Option(help="Optional local JSON report path; stdout is always emitted."),
    ] = None,
) -> None:
    """Evaluate selected T0 cycles offline; never send an alert or an order."""
    try:
        request = SimulationRequest.model_validate_json(input_file.read_text(encoding="utf-8"))
        report = simulate_cycles(request)
    except (OSError, ValidationError, ValueError) as error:
        typer.echo(f"T0 cycle simulation rejected: {error}", err=True)
        raise typer.Exit(code=1) from error
    _emit_model(report, output)
    if report.status == "INCOMPLETE":
        raise typer.Exit(code=INELIGIBLE_EXIT_CODE)


def audit_shadow_journal_command(
    manifest: Annotated[
        Path,
        typer.Option(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Local committed shadow journal manifest.",
        ),
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
    """Prove a local shadow journal is byte-identical to immutable S3 replay."""
    try:
        report = audit_shadow_journal(
            manifest,
            boto3.client("s3", region_name=region),
            load_configuration(config),
        )
    except (
        ShadowArbitrationAuditError,
        StreamCaptureReadError,
        TradingConfigurationError,
        ValueError,
    ) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except (CaptureStoreUnavailable, ClientError) as error:
        typer.echo(f"SSI shadow journal audit failed: {_safe_error(error)}", err=True)
        raise typer.Exit(code=1) from None
    _emit_model(report, output)


def certify_stream_day_command(
    trade_date: Annotated[
        str,
        typer.Option(help="Exchange-local trade date in YYYY-MM-DD format."),
    ],
    landing_uri: Annotated[str, typer.Option(help="Landing S3 root URI.")],
    region: Annotated[str, typer.Option(help="AWS region containing the landing bucket.")] = (
        "ap-southeast-1"
    ),
    config: Annotated[Path, typer.Option(help="Versioned non-secret trading YAML.")] = (
        DEFAULT_TRADING_CONFIG
    ),
) -> None:
    """Certify one immutable SSI Stream trade date for features and backtesting."""
    parsed_trade_date = _parse_trade_date(trade_date)
    try:
        version = load_configuration(config).resolve(parsed_trade_date)
        readers = _stream_day_readers(
            parsed_trade_date,
            landing_uri,
            region,
        )
        certification, _ = certify_market_day(
            readers,
            version,
            trade_date=parsed_trade_date,
        )
    except (
        StreamCaptureReadError,
        TradingConfigurationError,
        ValueError,
    ) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except Exception as error:  # SDK/AWS boundary: never print provider payload or credentials.
        typer.echo(f"SSI stream certification failed: {_safe_error(error)}", err=True)
        raise typer.Exit(code=1) from None
    _emit_market_day_certification(certification)


def validate_stream_day_command(
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
) -> None:
    """Validate terminal manifests for one observed SSI Stream trade date."""
    parsed_trade_date = _parse_trade_date(trade_date)
    environment = os.environ.get("LAKEHOUSE_ENVIRONMENT", "dev")
    effective_secret_id = secret_id or f"lakehouse/{environment}/t0-trading/ssi"
    try:
        credentials = load_credentials(effective_secret_id, region)
        with authenticated(credentials) as auth, Data(auth) as data:
            require_observed_trade_date(data.market_data, trade_date=parsed_trade_date)
        readers = _stream_day_readers(
            parsed_trade_date,
            landing_uri,
            region,
        )
        message_count = sum(reader.manifest.message_count for reader in readers)
    except TradingDateError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=99) from error
    except (CredentialError, StreamCaptureReadError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    except Exception as error:  # SDK/AWS boundary: never print provider payload or credentials.
        typer.echo(f"SSI stream validation failed: {_safe_error(error)}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        json.dumps(
            {
                "manifest_count": len(readers),
                "message_count": message_count,
                "trade_date": parsed_trade_date.isoformat(),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


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
app.command("audit-buy-first-baselines")(audit_buy_first_baselines_command)
app.command("audit-baseline-walk-forward")(audit_baseline_walk_forward_command)
app.command("simulate-cycles")(simulate_cycles_command)
app.command("audit-shadow-journal")(audit_shadow_journal_command)
app.command("validate-stream-day")(validate_stream_day_command)
app.command("certify-stream-day")(certify_stream_day_command)
