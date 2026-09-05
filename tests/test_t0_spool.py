from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from t0_trading.capture.spool import CaptureSpool, SpoolFullError
from t0_trading.capture.store import canonical_json, sha256


class _Store:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.objects: dict[str, bytes] = {}
        self.published: list[str] = []

    def uri(self, key: str) -> str:
        return f"s3://landing/{key}"

    def read_json(self, key: str) -> dict[str, Any] | None:
        return None

    def _put(self, key: str, body: bytes) -> tuple[str, str]:
        if not self.available:
            raise ConnectionError("S3 unavailable")
        current = self.objects.get(key)
        if current is not None and current != body:
            raise RuntimeError("immutable object conflict")
        self.objects[key] = body
        self.published.append(key)
        return key, sha256(body)

    def put_json(self, key: str, value: Mapping[str, Any]) -> tuple[str, str]:
        return self._put(key, canonical_json(value))

    def put_capture(self, key: str, body: bytes) -> tuple[str, str]:
        return self._put(key, body)


def test_spool_retains_failed_object_and_replays_it_after_restart(tmp_path: Path) -> None:
    key = "stream/source/raw/trade_date=2026-09-04/session=one/batches/one.json.gz"
    body = b"capture"
    store = _Store(available=False)
    spool = CaptureSpool(tmp_path, max_bytes=1024)
    spool.stage_capture(key, body)

    with pytest.raises(ConnectionError, match="S3 unavailable"):
        spool.drain(store)

    assert spool.pending_bytes == len(body)
    store.available = True
    restarted = CaptureSpool(tmp_path, max_bytes=1024)
    assert restarted.drain(store) == 1
    assert restarted.pending_bytes == 0
    assert store.objects[key] == body


def test_spool_publishes_batches_before_their_terminal_manifest(tmp_path: Path) -> None:
    prefix = "stream/source/raw/trade_date=2026-09-04/session=one"
    batch_key = f"{prefix}/batches/one.json.gz"
    manifest_key = f"{prefix}/manifest.json"
    spool = CaptureSpool(tmp_path, max_bytes=1024)
    spool.stage_json(manifest_key, {"schema_version": 1})
    spool.stage_capture(batch_key, b"capture")
    store = _Store()

    assert spool.drain(store) == 2

    assert store.published == [batch_key, manifest_key]


def test_spool_is_bounded_and_rejects_conflicting_local_content(tmp_path: Path) -> None:
    key = "stream/source/raw/trade_date=2026-09-04/session=one/batches/one.json.gz"
    spool = CaptureSpool(tmp_path, max_bytes=7)
    spool.stage_capture(key, b"capture")

    with pytest.raises(RuntimeError, match="spool conflict"):
        spool.stage_capture(key, b"changed")
    with pytest.raises(SpoolFullError, match="capacity exceeded"):
        spool.stage_capture(key.replace("one.json.gz", "two.json.gz"), b"x")
