"""Idempotent shadow-journal recovery from certified terminal capture."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from t0_trading.arbitration.audit import (
    ShadowArbitrationAuditReport,
    audit_shadow_journal,
)
from t0_trading.arbitration.journal import ShadowArbitrationJournal
from t0_trading.arbitration.publication import publish_shadow_journal
from t0_trading.capture.reader import StreamDayReader
from t0_trading.capture.store import CaptureStore
from t0_trading.configuration import TradingConfiguration
from t0_trading.evidence_paths import shadow_journal_manifest_key
from t0_trading.market import StreamEnvelope


class ShadowJournalEnsureResult(BaseModel):
    """Outcome of ensuring one immutable journal from its exact capture lineage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    action: Literal["EXISTING", "REBUILT"]
    trade_date: date
    manifest_uri: str
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_count: int = Field(ge=1)
    arbitration_count: int = Field(ge=1)


def _result(
    action: Literal["EXISTING", "REBUILT"],
    manifest_uri: str,
    report: ShadowArbitrationAuditReport,
) -> ShadowJournalEnsureResult:
    return ShadowJournalEnsureResult(
        action=action,
        trade_date=report.trade_date,
        manifest_uri=manifest_uri,
        manifest_sha256=report.manifest_sha256,
        capture_evidence_sha256=report.capture_evidence_sha256,
        candidate_count=report.candidate_count,
        arbitration_count=report.arbitration_count,
    )


def _replay_journal(
    capture: StreamDayReader,
    configuration: TradingConfiguration,
    output: Path,
) -> ShadowArbitrationJournal:
    trade_date = capture.trade_date
    version = configuration.resolve(trade_date)
    arbitration = configuration.resolve_candidate_arbitration(trade_date)
    if arbitration is None:
        raise ValueError("no prospective candidate-arbitration policy covers this session")

    errors: list[Exception] = []
    journal = ShadowArbitrationJournal(
        output,
        trade_date,
        version,
        configuration.resolve_context(trade_date),
        arbitration,
        breadth_policy=configuration.resolve_breadth(trade_date),
        regime_policy=configuration.resolve_regime(trade_date),
        breadth_membership=capture.breadth_membership,
        on_error=errors.append,
    )
    watermark = timedelta(seconds=version.features.cadence_seconds)
    for session in capture.sessions:
        journal.connected(
            session.manifest.stream_session_id,
            session.manifest.connected_at,
        )
        receipt_group: list[StreamEnvelope] = []
        for envelope in session.envelopes():
            if receipt_group and envelope.received_at != receipt_group[-1].received_at:
                journal.ingest(receipt_group)
                journal.advance(receipt_group[-1].received_at + watermark)
                receipt_group.clear()
            receipt_group.append(envelope)
        if receipt_group:
            journal.ingest(receipt_group)
            journal.advance(receipt_group[-1].received_at + watermark)
        unavailable = session.manifest.disconnect_kind not in {"completed", "shutdown"}
        journal.disconnected(
            session.covered_until if unavailable else session.manifest.disconnected_at,
            unavailable=unavailable,
        )

    completed_at = max(session.manifest.published_at for session in capture.sessions)
    journal.close(completed_at, capture.manifest_uris)
    if journal.failed:
        error = errors[0] if errors else RuntimeError("unknown journal replay failure")
        raise RuntimeError(f"shadow journal replay failed ({type(error).__name__})") from error
    return journal


def ensure_shadow_journal(
    capture: StreamDayReader,
    configuration: TradingConfiguration,
    store: CaptureStore,
    s3_client: Any,
    workspace: Path,
) -> ShadowJournalEnsureResult:
    """Audit an existing journal or rebuild it once from certified raw evidence."""
    manifest_key = shadow_journal_manifest_key(capture.trade_date)
    manifest_uri = store.uri(manifest_key)
    if store.read_json(manifest_key) is not None:
        report = audit_shadow_journal(
            manifest_uri, s3_client, configuration, certified_capture=capture
        )
        return _result("EXISTING", manifest_uri, report)

    journal = _replay_journal(
        capture,
        configuration,
        workspace / "shadow.arbitrations.jsonl",
    )
    published_uri, _ = publish_shadow_journal(journal, store)
    if published_uri != manifest_uri:
        raise RuntimeError("shadow journal publication escaped its canonical location")
    report = audit_shadow_journal(manifest_uri, s3_client, configuration, certified_capture=capture)
    return _result("REBUILT", manifest_uri, report)
