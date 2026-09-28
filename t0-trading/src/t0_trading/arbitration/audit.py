"""Read-only replay audit for an arbitration-only shadow journal."""

from __future__ import annotations

import gzip
import io
from datetime import date
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from t0_trading.arbitration.engine import arbitrate_candidates
from t0_trading.arbitration.journal import ShadowArbitrationManifest
from t0_trading.capture.reader import StreamDayReader, StreamSessionReader
from t0_trading.capture.store import CaptureStoreUnavailable, S3CaptureStore
from t0_trading.configuration import TradingConfiguration
from t0_trading.context import build_decision_contexts
from t0_trading.evidence_paths import shadow_journal_manifest_key
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
    """Proof that immutable candidate/arbitration output equals deterministic replay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[3] = 3
    status: Literal["passed"] = "passed"
    trade_date: date
    baseline_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    arbitration_version: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    arbitration_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stream_session_ids: tuple[str, ...]
    capture_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_message_count: int = Field(ge=0)
    gap_count: int = Field(ge=0)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_count: int = Field(ge=1)
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    arbitration_count: int = Field(ge=1)
    arbitration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _load_manifest(
    manifest_uri: str, s3_client: Any
) -> tuple[ShadowArbitrationManifest, bytes, S3CaptureStore, str]:
    parsed = urlparse(manifest_uri)
    key = parsed.path.lstrip("/")
    if parsed.scheme != "s3" or not parsed.netloc or not key.endswith("/manifest.json"):
        raise ShadowArbitrationAuditError("shadow journal manifest URI is invalid")
    store = S3CaptureStore(s3_client, f"s3://{parsed.netloc}")
    try:
        body = store.read_capture(key)
        if body is None:
            raise ShadowArbitrationAuditError("shadow journal manifest does not exist")
        manifest = ShadowArbitrationManifest.model_validate_json(body)
    except ShadowArbitrationAuditError:
        raise
    except (RuntimeError, ValueError) as error:
        raise ShadowArbitrationAuditError("shadow journal manifest is invalid") from error
    if body != manifest.canonical_bytes() or not key.endswith(
        shadow_journal_manifest_key(manifest.trade_date)
    ):
        raise ShadowArbitrationAuditError("shadow journal manifest is not canonical")
    return manifest, body, store, key.rsplit("/", maxsplit=1)[0]


def _read_journal(store: S3CaptureStore, key: str, label: str) -> bytes:
    try:
        compressed = store.read_capture(key)
        if compressed is None:
            raise ShadowArbitrationAuditError(f"shadow {label} journal does not exist")
        return gzip.decompress(compressed)
    except ShadowArbitrationAuditError:
        raise
    except (OSError, RuntimeError) as error:
        raise ShadowArbitrationAuditError(f"shadow {label} journal is invalid") from error


def _verify_replay(body: bytes, records: tuple[Any, ...], label: str) -> None:
    journal = io.BytesIO(body)
    for record in records:
        if journal.readline() != record.canonical_bytes() + b"\n":
            raise ShadowArbitrationAuditError(f"shadow {label} journal differs from replay")
    if journal.read(1):
        raise ShadowArbitrationAuditError(f"shadow {label} journal contains trailing records")


def audit_shadow_journal(
    manifest_uri: str,
    s3_client: Any,
    configuration: TradingConfiguration,
) -> ShadowArbitrationAuditReport:
    """Prove one committed journal against immutable capture and policy lineage."""
    manifest, manifest_body, store, root = _load_manifest(manifest_uri, s3_client)
    if not (
        manifest.candidate_file.endswith(".candidates.jsonl.gz")
        and manifest.arbitration_file.endswith(".arbitrations.jsonl.gz")
    ):
        raise ShadowArbitrationAuditError("shadow journal object lineage is inconsistent")
    candidate_body = _read_journal(store, f"{root}/{manifest.candidate_file}", "candidate")
    arbitration_body = _read_journal(store, f"{root}/{manifest.arbitration_file}", "arbitration")
    candidate_sha256 = sha256(candidate_body)
    arbitration_sha256 = sha256(arbitration_body)
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
    _verify_replay(candidate_body, candidates, "candidate")
    _verify_replay(arbitration_body, arbitrations, "arbitration")

    return ShadowArbitrationAuditReport(
        trade_date=manifest.trade_date,
        baseline_version=manifest.baseline_version,
        arbitration_version=manifest.arbitration_version,
        arbitration_configuration_sha256=manifest.arbitration_configuration_sha256,
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
