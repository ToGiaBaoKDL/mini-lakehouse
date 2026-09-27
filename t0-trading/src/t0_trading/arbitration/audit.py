"""Read-only replay audit for an arbitration-only shadow journal."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from t0_trading.arbitration.engine import arbitrate_candidates
from t0_trading.arbitration.journal import ShadowArbitrationManifest
from t0_trading.capture.reader import StreamDayReader, StreamSessionReader
from t0_trading.capture.store import CaptureStoreUnavailable
from t0_trading.configuration import TradingConfiguration
from t0_trading.context import build_decision_contexts
from t0_trading.features import decision_times, replay_features
from t0_trading.identity import sha256
from t0_trading.strategy.baselines import (
    BASELINE_NAMES,
    BASELINE_VERSION,
    score_buy_first_baselines,
)


class ShadowArbitrationAuditError(RuntimeError):
    """A committed shadow journal cannot be proven from its declared evidence."""


class ShadowArbitrationAuditReport(BaseModel):
    """Proof that local candidate/arbitration output equals deterministic S3 replay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = 2
    status: Literal["passed"] = "passed"
    trade_date: date
    stream_session_ids: tuple[str, ...]
    capture_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_message_count: int = Field(ge=0)
    gap_count: int = Field(ge=0)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_count: int = Field(ge=1)
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    arbitration_count: int = Field(ge=1)
    arbitration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _load_manifest(path: Path) -> tuple[ShadowArbitrationManifest, bytes]:
    if path.is_symlink() or not path.is_file() or not path.name.endswith(".manifest.json"):
        raise ShadowArbitrationAuditError("shadow journal manifest path is invalid")
    try:
        body = path.read_bytes()
        manifest = ShadowArbitrationManifest.model_validate_json(body)
    except (OSError, ValueError) as error:
        raise ShadowArbitrationAuditError("shadow journal manifest is invalid") from error
    if body != manifest.canonical_bytes():
        raise ShadowArbitrationAuditError("shadow journal manifest is not canonical")
    expected_name = manifest.arbitration_file.removesuffix(".arbitrations.jsonl") + ".manifest.json"
    if path.name != expected_name:
        raise ShadowArbitrationAuditError("shadow journal manifest file lineage is inconsistent")
    return manifest, body


def _journal_digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ShadowArbitrationAuditError("shadow journal does not exist")
    try:
        with path.open("rb") as journal:
            return hashlib.file_digest(journal, "sha256").hexdigest()
    except OSError as error:
        raise ShadowArbitrationAuditError("shadow journal cannot be read") from error


def _verify_replay(path: Path, records: tuple[Any, ...], label: str) -> None:
    try:
        with path.open("rb") as journal:
            for record in records:
                if journal.readline() != record.canonical_bytes() + b"\n":
                    raise ShadowArbitrationAuditError(f"shadow {label} journal differs from replay")
            if journal.read(1):
                raise ShadowArbitrationAuditError(
                    f"shadow {label} journal contains trailing records"
                )
    except OSError as error:
        raise ShadowArbitrationAuditError(f"shadow {label} journal cannot be read") from error


def audit_shadow_journal(
    manifest_path: Path,
    s3_client: Any,
    configuration: TradingConfiguration,
) -> ShadowArbitrationAuditReport:
    """Prove one committed journal against immutable capture and policy lineage."""
    manifest, manifest_body = _load_manifest(manifest_path)
    candidate_path = manifest_path.with_name(manifest.candidate_file)
    arbitration_path = manifest_path.with_name(manifest.arbitration_file)
    candidate_sha256 = _journal_digest(candidate_path)
    arbitration_sha256 = _journal_digest(arbitration_path)
    if candidate_sha256 != manifest.candidate_sha256:
        raise ShadowArbitrationAuditError("shadow candidate journal checksum mismatch")
    if arbitration_sha256 != manifest.arbitration_sha256:
        raise ShadowArbitrationAuditError("shadow arbitration journal checksum mismatch")

    version = configuration.resolve(manifest.trade_date)
    context_policy = configuration.resolve_context(manifest.trade_date)
    arbitration_policy = configuration.resolve_candidate_arbitration(manifest.trade_date)
    if arbitration_policy is None:
        raise ShadowArbitrationAuditError(
            "shadow journal policy lineage does not match configuration"
        )
    if (
        manifest.configuration_version,
        manifest.configuration_sha256,
        manifest.feature_version,
        manifest.context_version,
        manifest.context_configuration_sha256,
        manifest.baseline_version,
        manifest.arbitration_version,
        manifest.arbitration_configuration_sha256,
    ) != (
        version.version,
        version.sha256,
        version.features.version,
        context_policy.version,
        context_policy.sha256,
        BASELINE_VERSION,
        arbitration_policy.version,
        arbitration_policy.sha256,
    ):
        raise ShadowArbitrationAuditError(
            "shadow journal policy lineage does not match configuration"
        )

    schedule = tuple(decision_times(version, manifest.trade_date))
    expected_count = len(schedule) * len(version.market.symbols) * len(BASELINE_NAMES)
    if (
        not schedule
        or manifest.first_connected_at > schedule[0]
        or manifest.completed_at < schedule[-1]
        or manifest.expected_candidate_count != expected_count
        or manifest.expected_arbitration_count != expected_count
    ):
        raise ShadowArbitrationAuditError("shadow journal decision-clock coverage is inconsistent")

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
        raise ShadowArbitrationAuditError("shadow capture evidence is invalid") from error
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
        raise ShadowArbitrationAuditError("shadow journal capture lineage is inconsistent")

    snapshots = replay_features(
        capture.envelopes(), version, trade_date=manifest.trade_date, gaps=capture.gaps
    )
    contexts = build_decision_contexts(snapshots, capture.envelopes(), version, context_policy)
    candidates = score_buy_first_baselines(snapshots, contexts)
    arbitrations = arbitrate_candidates(candidates, arbitration_policy)
    if len(candidates) != manifest.candidate_count:
        raise ShadowArbitrationAuditError("shadow candidate summary differs from replay")
    if len(arbitrations) != manifest.arbitration_count:
        raise ShadowArbitrationAuditError("shadow arbitration summary differs from replay")
    _verify_replay(candidate_path, candidates, "candidate")
    _verify_replay(arbitration_path, arbitrations, "arbitration")

    return ShadowArbitrationAuditReport(
        trade_date=manifest.trade_date,
        stream_session_ids=manifest.stream_session_ids,
        capture_evidence_sha256=capture.evidence_sha256,
        capture_message_count=capture.message_count,
        gap_count=len(capture.gaps),
        manifest_sha256=sha256(manifest_body),
        candidate_count=manifest.candidate_count,
        candidate_sha256=candidate_sha256,
        arbitration_count=manifest.arbitration_count,
        arbitration_sha256=arbitration_sha256,
    )
