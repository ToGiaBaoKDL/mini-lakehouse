"""Read-only parity audit for one committed local shadow journal."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from t0_trading.capture.reader import StreamDayReader, StreamSessionReader
from t0_trading.capture.store import CaptureStoreUnavailable
from t0_trading.configuration import TradingConfiguration
from t0_trading.decisions.engine import replay_decisions
from t0_trading.decisions.model import DECISION_ACTIONS, DecisionAction
from t0_trading.decisions.shadow import ShadowJournalManifest
from t0_trading.features import decision_times, replay_features
from t0_trading.identity import sha256


class ShadowJournalAuditError(RuntimeError):
    """A committed shadow journal cannot be proven from its declared evidence."""


class ShadowJournalAuditReport(BaseModel):
    """Compact proof that local shadow output equals deterministic S3 replay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    status: Literal["passed"] = "passed"
    trade_date: date
    stream_session_ids: tuple[str, ...]
    capture_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_message_count: int = Field(ge=0)
    gap_count: int = Field(ge=0)
    decision_count: int = Field(ge=1)
    action_counts: dict[DecisionAction, int]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    journal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _load_manifest(path: Path) -> tuple[ShadowJournalManifest, bytes]:
    if path.is_symlink() or not path.is_file() or not path.name.endswith(".manifest.json"):
        raise ShadowJournalAuditError("shadow journal manifest path is invalid")
    try:
        body = path.read_bytes()
        manifest = ShadowJournalManifest.model_validate_json(body)
    except (OSError, ValueError) as error:
        raise ShadowJournalAuditError("shadow journal manifest is invalid") from error
    if body != manifest.canonical_bytes():
        raise ShadowJournalAuditError("shadow journal manifest is not canonical")
    expected_name = Path(manifest.journal_file).with_suffix(".manifest.json").name
    if path.name != expected_name:
        raise ShadowJournalAuditError("shadow journal manifest file lineage is inconsistent")
    return manifest, body


def _journal_digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ShadowJournalAuditError("shadow decision journal does not exist")
    try:
        with path.open("rb") as journal:
            return hashlib.file_digest(journal, "sha256").hexdigest()
    except OSError as error:
        raise ShadowJournalAuditError("shadow decision journal cannot be read") from error


def audit_shadow_journal(
    manifest_path: Path,
    s3_client: Any,
    configuration: TradingConfiguration,
) -> ShadowJournalAuditReport:
    """Prove one committed local journal against its immutable capture and policy lineage."""
    manifest, manifest_body = _load_manifest(manifest_path)
    journal_path = manifest_path.with_name(manifest.journal_file)
    journal_sha256 = _journal_digest(journal_path)
    if journal_sha256 != manifest.journal_sha256:
        raise ShadowJournalAuditError(
            "shadow decision journal checksum does not match its manifest"
        )

    version = configuration.resolve(manifest.trade_date)
    strategy_policy = configuration.resolve_strategies(manifest.trade_date)
    outcome_policy = configuration.resolve_outcomes(manifest.trade_date)
    decision_policy = configuration.resolve_decisions(manifest.trade_date)
    observed_lineage = (
        manifest.configuration_version,
        manifest.configuration_sha256,
        manifest.feature_version,
        manifest.strategy_version,
        manifest.strategy_configuration_sha256,
        manifest.outcome_version,
        manifest.outcome_configuration_sha256,
        manifest.decision_version,
        manifest.decision_configuration_sha256,
    )
    expected_lineage = (
        version.version,
        version.sha256,
        version.features.version,
        strategy_policy.version,
        strategy_policy.sha256,
        outcome_policy.version,
        outcome_policy.sha256,
        decision_policy.version,
        decision_policy.sha256,
    )
    if observed_lineage != expected_lineage:
        raise ShadowJournalAuditError("shadow journal policy lineage does not match configuration")

    schedule = tuple(decision_times(version, manifest.trade_date))
    expected_count = len(schedule) * len(version.market.symbols) * len(decision_policy.rules)
    if (
        not schedule
        or manifest.first_connected_at > schedule[0]
        or manifest.completed_at < schedule[-1]
        or manifest.expected_decision_count != expected_count
    ):
        raise ShadowJournalAuditError("shadow journal decision-clock coverage is inconsistent")

    try:
        capture = StreamDayReader(
            tuple(
                StreamSessionReader.from_uri(s3_client, uri)
                for uri in manifest.capture_manifest_uris
            )
        )
    except CaptureStoreUnavailable:
        raise
    except (RuntimeError, ValueError) as error:
        raise ShadowJournalAuditError("shadow journal capture evidence is invalid") from error
    if (
        capture.trade_date != manifest.trade_date
        or capture.symbols != version.market.symbols
        or capture.stream_session_ids != manifest.stream_session_ids
        or capture.manifest_uris != manifest.capture_manifest_uris
        or capture.connected_at != manifest.first_connected_at
        or capture.disconnected_at > manifest.completed_at
        or any(
            session.manifest.published_at > manifest.completed_at for session in capture.sessions
        )
    ):
        raise ShadowJournalAuditError("shadow journal capture lineage is inconsistent")

    snapshots = replay_features(
        capture.envelopes(),
        version,
        trade_date=manifest.trade_date,
        gaps=capture.gaps,
    )
    decisions = replay_decisions(
        snapshots,
        version,
        strategy_policy,
        outcome_policy,
        decision_policy,
    )
    action_counts = Counter(decision.action for decision in decisions)
    replay_counts = {action: action_counts[action] for action in DECISION_ACTIONS}
    if len(decisions) != manifest.decision_count or replay_counts != manifest.action_counts:
        raise ShadowJournalAuditError("shadow journal decision summary differs from replay")

    try:
        with journal_path.open("rb") as journal:
            for decision in decisions:
                if journal.readline() != decision.canonical_bytes() + b"\n":
                    raise ShadowJournalAuditError("shadow decision journal differs from replay")
            if journal.read(1):
                raise ShadowJournalAuditError("shadow decision journal contains trailing records")
    except OSError as error:
        raise ShadowJournalAuditError("shadow decision journal cannot be read") from error

    return ShadowJournalAuditReport(
        trade_date=manifest.trade_date,
        stream_session_ids=manifest.stream_session_ids,
        capture_evidence_sha256=capture.evidence_sha256,
        capture_message_count=capture.message_count,
        gap_count=len(capture.gaps),
        decision_count=manifest.decision_count,
        action_counts=manifest.action_counts,
        manifest_sha256=sha256(manifest_body),
        journal_sha256=journal_sha256,
    )
