"""Publish an ephemeral shadow journal as immutable S3 evidence."""

from __future__ import annotations

import gzip

from t0_trading.arbitration.journal import ShadowArbitrationJournal
from t0_trading.capture.spool import CaptureSpool
from t0_trading.capture.store import CaptureStore
from t0_trading.evidence_paths import shadow_journal_manifest_key, shadow_journal_root
from t0_trading.identity import sha256


def publish_shadow_journal(
    journal: ShadowArbitrationJournal,
    store: CaptureStore,
    *,
    spool: CaptureSpool | None = None,
) -> tuple[str, str]:
    """Publish journals before their manifest commit marker, optionally via the outbox."""
    manifest = journal.manifest
    if manifest is None or journal.failed:
        raise RuntimeError("shadow journal is not complete")
    candidate_body = journal.candidate_output.read_bytes()
    arbitration_body = journal.output.read_bytes()
    if (
        sha256(candidate_body) != manifest.candidate_sha256
        or sha256(arbitration_body) != manifest.arbitration_sha256
    ):
        raise RuntimeError("shadow journal changed before publication")

    published_manifest = manifest.model_copy(
        update={
            "candidate_file": f"{manifest.candidate_file}.gz",
            "arbitration_file": f"{manifest.arbitration_file}.gz",
        }
    )
    root = shadow_journal_root(manifest.trade_date)
    candidate_key = f"{root}/{published_manifest.candidate_file}"
    arbitration_key = f"{root}/{published_manifest.arbitration_file}"
    manifest_key = shadow_journal_manifest_key(manifest.trade_date)
    candidate_capture = gzip.compress(candidate_body, mtime=0)
    arbitration_capture = gzip.compress(arbitration_body, mtime=0)

    if spool is not None:
        spool.stage_capture(candidate_key, candidate_capture)
        spool.stage_capture(arbitration_key, arbitration_capture)
        spool.stage_json(manifest_key, published_manifest.model_dump(mode="json"))
        spool.drain(store)
    else:
        _, candidate_digest = store.put_capture(candidate_key, candidate_capture)
        _, arbitration_digest = store.put_capture(arbitration_key, arbitration_capture)
        _, manifest_digest = store.put_json(
            manifest_key, published_manifest.model_dump(mode="json")
        )
        if (
            candidate_digest != sha256(candidate_capture)
            or arbitration_digest != sha256(arbitration_capture)
            or manifest_digest != published_manifest.sha256
        ):
            raise RuntimeError("shadow journal publication checksum is inconsistent")
    return store.uri(manifest_key), published_manifest.sha256
