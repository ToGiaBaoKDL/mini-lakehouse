import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from emr_jobs.market_data import stream_manifest
from emr_jobs.market_data.capture_manifest import canonical_json

from lakehouse.contracts import load_contracts

RAW_PREFIX = load_contracts().source("ssi_fastconnect_stream").raw_object_prefix
SESSION_ID = "6b710ea5-f0eb-457e-bb58-73961428670a"
TRADE_DATE = "2026-09-03"


def _capture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mutate: Callable[[dict[str, object]], None] | None = None,
    manifest_metadata_sha256: str | None = None,
) -> str:
    connected_at = datetime(2026, 9, 3, 6, 32, tzinfo=UTC).isoformat()
    disconnected_at = datetime(2026, 9, 3, 6, 42, tzinfo=UTC).isoformat()
    batch_body = b"batch"
    batch_sha256 = hashlib.sha256(batch_body).hexdigest()
    session_prefix = f"{RAW_PREFIX}/trade_date={TRADE_DATE}/session={SESSION_ID}"
    batch_key = f"{session_prefix}/batches/{1:012d}-{2:012d}-{batch_sha256}.json.gz"
    batch_id = hashlib.sha256(
        canonical_json(
            {
                "stream_session_id": SESSION_ID,
                "first_receive_sequence": 1,
                "last_receive_sequence": 2,
                "object_sha256": batch_sha256,
            }
        )
    ).hexdigest()
    manifest: dict[str, object] = {
        "schema_version": 1,
        "stream_session_id": SESSION_ID,
        "symbols": ["VIC", "VHM"],
        "connected_at": connected_at,
        "disconnected_at": disconnected_at,
        "disconnect_kind": "completed",
        "message_count": 2,
        "first_receive_sequence": 1,
        "last_receive_sequence": 2,
        "heartbeat_count": 20,
        "last_heartbeat_at": "2026-09-03T06:41:30+00:00",
        "last_business_message_at": "2026-09-03T06:41:59+00:00",
        "batch_count": 1,
        "batches": [
            {
                "batch_id": batch_id,
                "first_receive_sequence": 1,
                "last_receive_sequence": 2,
                "message_count": 2,
                "object_key": batch_key,
                "object_sha256": batch_sha256,
                "published_at": "2026-09-03T06:40:00+00:00",
            }
        ],
        "api_version": "v3",
        "sdk_version": "3.2.1",
        "error_type": None,
        "published_at": "2026-09-03T06:42:01+00:00",
    }
    if mutate is not None:
        mutate(manifest)
    body = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    manifest_key = f"{session_prefix}/manifest.json"
    uri = f"s3://landing/root/{manifest_key}"

    def read_bytes(_uri: str) -> bytes:
        return body

    monkeypatch.setattr(stream_manifest, "read_bytes", read_bytes)

    def head_object(bucket: str, key: str) -> dict[str, object] | None:
        if bucket != "landing":
            return None
        if key == f"root/{manifest_key}":
            return {
                "ContentLength": len(body),
                "Metadata": {
                    "sha256": manifest_metadata_sha256 or hashlib.sha256(body).hexdigest()
                },
            }
        if key == f"root/{batch_key}":
            return {
                "ContentLength": len(batch_body),
                "Metadata": {"sha256": batch_sha256},
            }
        return None

    monkeypatch.setattr(stream_manifest, "head_object", head_object)
    return uri


def test_stream_manifest_resolves_verified_terminal_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = stream_manifest.load_capture(_capture(monkeypatch), RAW_PREFIX)

    assert capture.trade_date == TRADE_DATE
    assert capture.stream_session_id == SESSION_ID
    assert capture.symbols == ("VIC", "VHM")
    assert capture.message_count == 2
    assert capture.batches[0].first_receive_sequence == 1
    assert capture.batches[0].uri.endswith(".json.gz")


def test_stream_manifest_rejects_its_own_checksum_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="manifest is missing or has checksum drift"):
        stream_manifest.load_capture(
            _capture(monkeypatch, manifest_metadata_sha256="0" * 64), RAW_PREFIX
        )


def test_stream_manifest_rejects_unclean_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mutate(manifest: dict[str, object]) -> None:
        manifest["disconnect_kind"] = "stale"
        manifest["error_type"] = "HeartbeatTimeout"

    with pytest.raises(RuntimeError, match="did not terminate cleanly"):
        stream_manifest.load_capture(_capture(monkeypatch, mutate=mutate), RAW_PREFIX)


def test_stream_manifest_rejects_sequence_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mutate(manifest: dict[str, object]) -> None:
        batches = manifest["batches"]
        assert isinstance(batches, list)
        batch = batches[0]
        assert isinstance(batch, dict)
        batch["first_receive_sequence"] = 2

    with pytest.raises(RuntimeError, match="batch lineage is inconsistent"):
        stream_manifest.load_capture(_capture(monkeypatch, mutate=mutate), RAW_PREFIX)
