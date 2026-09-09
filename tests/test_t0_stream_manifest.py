import hashlib
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import pytest
from emr_jobs.market_data.stream_capture import discover_captures, load_capture
from t0_trading.capture.reader import StreamCaptureReadError
from t0_trading.identity import canonical_json

from lakehouse.contracts import load_contracts

RAW_PREFIX = load_contracts().source("ssi_fastconnect_stream").raw_object_prefix
SESSION_ID = "6b710ea5-f0eb-457e-bb58-73961428670a"
TRADE_DATE = date(2026, 9, 3)


class _Body:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body


class _Paginator:
    def __init__(self, pages: tuple[dict[str, object], ...]) -> None:
        self._pages = pages

    def paginate(self, **_kwargs: str) -> tuple[dict[str, object], ...]:
        return self._pages


class _S3:
    def __init__(
        self,
        objects: dict[str, bytes],
        metadata: dict[str, str],
        *,
        pages: tuple[dict[str, object], ...] = (),
    ) -> None:
        self._objects = objects
        self._metadata = metadata
        self._pages = pages

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        assert Bucket == "landing"
        return {
            "Body": _Body(self._objects[Key]),
            "Metadata": {"sha256": self._metadata[Key]},
        }

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        assert Bucket == "landing"
        return {
            "ContentLength": len(self._objects[Key]),
            "Metadata": {"sha256": self._metadata[Key]},
        }

    def get_paginator(self, name: str) -> _Paginator:
        assert name == "list_objects_v2"
        return _Paginator(self._pages)


def _capture(
    *,
    mutate: Callable[[dict[str, object]], None] | None = None,
    manifest_checksum: str | None = None,
    batch_checksum: str | None = None,
) -> tuple[_S3, str, str]:
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
    manifest_body = canonical_json(manifest)
    manifest_key = f"{session_prefix}/manifest.json"
    physical_manifest_key = f"root/{manifest_key}"
    physical_batch_key = f"root/{batch_key}"
    expected_manifest_sha256 = hashlib.sha256(manifest_body).hexdigest()
    client = _S3(
        {
            physical_manifest_key: manifest_body,
            physical_batch_key: batch_body,
        },
        {
            physical_manifest_key: manifest_checksum or expected_manifest_sha256,
            physical_batch_key: batch_checksum or batch_sha256,
        },
    )
    return client, f"s3://landing/{physical_manifest_key}", expected_manifest_sha256


def test_stream_capture_discovery_delegates_to_the_canonical_reader() -> None:
    day_prefix = f"root/{RAW_PREFIX}/trade_date={TRADE_DATE}/"
    first = f"{day_prefix}session=aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/manifest.json"
    second = f"{day_prefix}session=bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/manifest.json"
    client = _S3(
        {},
        {},
        pages=(
            {
                "Contents": [
                    {"Key": second},
                    {"Key": f"{day_prefix}session=ignored/batches/one.json.gz"},
                    {"Key": first},
                ]
            },
        ),
    )

    assert discover_captures(
        client,
        landing_uri="s3://landing/root",
        trade_date=TRADE_DATE,
        raw_object_prefix=RAW_PREFIX,
    ) == (f"s3://landing/{first}", f"s3://landing/{second}")
    with pytest.raises(RuntimeError, match="raw prefixes disagree"):
        discover_captures(
            client,
            landing_uri="s3://landing/root",
            trade_date=TRADE_DATE,
            raw_object_prefix="wrong/prefix",
        )


def test_stream_capture_uses_one_manifest_model_and_preserves_lineage() -> None:
    client, uri, expected_manifest_sha256 = _capture()

    capture = load_capture(client, uri)

    assert capture.trade_date == TRADE_DATE
    assert capture.uri == uri
    assert capture.manifest.stream_session_id == SESSION_ID
    assert capture.manifest.symbols == ("VIC", "VHM")
    assert capture.manifest_sha256 == expected_manifest_sha256
    assert capture.batch_uri(capture.manifest.batches[0]).endswith(".json.gz")


@pytest.mark.parametrize("target", ["manifest", "batch"])
def test_stream_capture_rejects_s3_checksum_drift(target: str) -> None:
    arguments: dict[str, Any] = {f"{target}_checksum": "0" * 64}
    client, uri, _ = _capture(**arguments)

    with pytest.raises(RuntimeError, match=r"checksum mismatch|checksum drift"):
        load_capture(client, uri)


def test_stream_capture_rejects_unclean_or_inconsistent_manifests() -> None:
    def unclean(manifest: dict[str, object]) -> None:
        manifest["disconnect_kind"] = "stale"
        manifest["error_type"] = "HeartbeatTimeout"

    client, uri, _ = _capture(mutate=unclean)
    with pytest.raises(StreamCaptureReadError, match="invalid SSI Stream manifest"):
        load_capture(client, uri)

    def gap(manifest: dict[str, object]) -> None:
        batches = manifest["batches"]
        assert isinstance(batches, list)
        assert isinstance(batches[0], dict)
        batches[0]["first_receive_sequence"] = 2

    client, uri, _ = _capture(mutate=gap)
    with pytest.raises(StreamCaptureReadError, match="invalid SSI Stream manifest"):
        load_capture(client, uri)
