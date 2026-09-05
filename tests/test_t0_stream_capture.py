import gzip
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from t0_trading.capture.spool import CaptureSpool
from t0_trading.capture.store import canonical_json, sha256
from t0_trading.capture.stream import (
    SSI_STREAM_RAW_PREFIX,
    StreamCaptureError,
    StreamCaptureOptions,
    capture_stream,
)


def test_stream_capture_defaults_bound_flush_latency_to_thirty_seconds() -> None:
    assert StreamCaptureOptions().flush_seconds == 30


class _Store:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.available = True

    def uri(self, key: str) -> str:
        return f"s3://landing/root/{key}"

    def read_json(self, key: str) -> dict[str, Any] | None:
        body = self.objects.get(key)
        if body is None:
            return None
        value = json.loads(body)
        assert isinstance(value, dict)
        return value

    def put_json(self, key: str, value: Mapping[str, Any]) -> tuple[str, str]:
        return self.put_capture(key, canonical_json(value))

    def put_capture(self, key: str, body: bytes) -> tuple[str, str]:
        if not self.available:
            raise ConnectionError("S3 unavailable")
        if key in self.objects:
            raise RuntimeError(f"Duplicate capture key: {key}")
        self.objects[key] = body
        return key, sha256(body)


class _Timer:
    def __init__(self) -> None:
        self.value = 0.0
        self.epoch = datetime(2026, 9, 3, 2, 0, tzinfo=UTC)

    def tick(self) -> float:
        return self.value

    def clock(self) -> datetime:
        return self.epoch + timedelta(seconds=self.value)


@dataclass
class _Trade:
    symbol: str
    trading_time: str
    price: int


class _Stream:
    def __init__(self, timer: _Timer, *, heartbeat: bool = True) -> None:
        self.timer = timer
        self.heartbeat = heartbeat
        self.on_data: Any = None
        self.on_heartbeat: Any = None
        self.is_connected = False
        self.pings = 0
        self._emitted = False

    def connect(self) -> None:
        self.is_connected = True

    def disconnect(self) -> None:
        self.is_connected = False

    def subscribe_symbol(self, symbols: list[str]) -> None:
        assert symbols == ["VIC", "VHM"]

    def subscribe_symbol_ohlcv(self, symbols: list[str], interval: Any) -> None:
        assert symbols == ["VIC", "VHM"]
        assert str(interval.value) == "1m"

    def ping(self) -> None:
        self.pings += 1

    def wait(self, timeout: float | None = None) -> None:
        self.timer.value += timeout or 0
        if not self._emitted:
            self._emitted = True
            self.on_data(_Trade("VIC", "09:00:00", 100))
            self.on_data(_Trade("VHM", "09:00:01", 101))
        if self.heartbeat:
            self.on_heartbeat({"message": "pong"})


def test_stream_capture_publishes_batches_and_one_terminal_manifest(tmp_path: Path) -> None:
    store = _Store()
    timer = _Timer()
    stream = _Stream(timer)
    ready: list[bool] = []

    manifest_uri = capture_stream(
        stream,
        store,
        StreamCaptureOptions(
            duration_seconds=1,
            heartbeat_seconds=0.25,
            stale_after_seconds=0.75,
            flush_seconds=0.5,
            batch_size=2,
            queue_size=10,
        ),
        clock=timer.clock,
        timer=timer.tick,
        session_id="session-1",
        on_ready=lambda: ready.append(True),
        spool=CaptureSpool(tmp_path, max_bytes=1024 * 1024),
    )

    assert manifest_uri.startswith(f"s3://landing/root/{SSI_STREAM_RAW_PREFIX}/")
    assert stream.is_connected is False
    assert stream.pings >= 2
    assert ready == [True]
    assert not any(tmp_path.rglob("*"))
    manifest_key = manifest_uri.removeprefix("s3://landing/root/")
    manifest = json.loads(store.objects[manifest_key])
    assert manifest["disconnect_kind"] == "completed"
    assert manifest["message_count"] == 2
    assert manifest["batch_count"] == 1
    assert manifest["heartbeat_count"] >= 1

    batch = manifest["batches"][0]
    body = store.objects[batch["object_key"]]
    rows = [json.loads(line) for line in gzip.decompress(body).splitlines()]
    assert [row["receive_sequence"] for row in rows] == [1, 2]
    assert [row["symbol"] for row in rows] == ["VIC", "VHM"]
    assert all("batch_id" not in row and "object_key" not in row for row in rows)
    assert batch["object_sha256"] == hashlib.sha256(body).hexdigest()


def test_stream_capture_fails_closed_when_heartbeats_are_stale() -> None:
    store = _Store()
    timer = _Timer()

    try:
        capture_stream(
            _Stream(timer, heartbeat=False),
            store,
            StreamCaptureOptions(
                duration_seconds=2,
                heartbeat_seconds=0.2,
                stale_after_seconds=0.5,
                flush_seconds=0.5,
                batch_size=10,
                queue_size=10,
            ),
            clock=timer.clock,
            timer=timer.tick,
            session_id="stale-session",
        )
    except StreamCaptureError as error:
        assert "stale" in str(error)
    else:
        raise AssertionError("Expected a missing heartbeat to fail the stream session")

    manifest_objects = [
        json.loads(body) for key, body in store.objects.items() if key.endswith("/manifest.json")
    ]
    assert manifest_objects[0]["disconnect_kind"] == "stale"
    assert manifest_objects[0]["error_type"] == "HeartbeatTimeout"


def test_stream_capture_spools_batch_and_terminal_manifest_during_s3_outage(
    tmp_path: Path,
) -> None:
    store = _Store()
    store.available = False
    timer = _Timer()
    spool = CaptureSpool(tmp_path, max_bytes=1024 * 1024)

    with pytest.raises(ConnectionError, match="S3 unavailable"):
        capture_stream(
            _Stream(timer),
            store,
            StreamCaptureOptions(
                duration_seconds=1,
                heartbeat_seconds=0.25,
                stale_after_seconds=0.75,
                flush_seconds=0.5,
                batch_size=2,
                queue_size=10,
            ),
            clock=timer.clock,
            timer=timer.tick,
            session_id="outage-session",
            spool=spool,
        )

    assert spool.pending_bytes > 0
    store.available = True
    restarted = CaptureSpool(tmp_path, max_bytes=1024 * 1024)
    assert restarted.drain(store) == 2
    manifest_key = next(key for key in store.objects if key.endswith("/manifest.json"))
    manifest = json.loads(store.objects[manifest_key])
    assert manifest["disconnect_kind"] == "capture_error"
    assert manifest["error_type"] == "ConnectionError"
    assert manifest["message_count"] == 2
