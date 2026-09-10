"""Streaming reader for one terminal SSI Stream capture."""

from __future__ import annotations

import gzip
import json
from collections import deque
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol
from urllib.parse import urlparse
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ssi_sdk import __version__ as SSI_SDK_VERSION

from t0_trading.capture import MAX_STREAM_BATCH_MESSAGES, SSI_STREAM_RAW_PREFIX
from t0_trading.capture.store import S3CaptureStore
from t0_trading.identity import canonical_json, sha256
from t0_trading.market.events import StreamEnvelope
from t0_trading.provider import SSI_API_VERSION

_MARKET_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")
StreamDisconnectKind = Literal["completed", "shutdown", "stale", "capture_error"]


class StreamCaptureReadError(RuntimeError):
    """A terminal stream capture violates its immutable storage contract."""


def stream_manifest_uris(client: Any, landing_uri: str, trade_date: date) -> tuple[str, ...]:
    """Discover direct terminal manifests under one source-owned trade-date prefix."""
    parsed = urlparse(landing_uri.rstrip("/"))
    if parsed.scheme != "s3" or not parsed.netloc:
        raise StreamCaptureReadError("landing_uri must be an S3 URI")
    logical_prefix = f"{SSI_STREAM_RAW_PREFIX}/trade_date={trade_date.isoformat()}/"
    physical_prefix = "/".join(part for part in (parsed.path.strip("/"), logical_prefix) if part)
    manifests: set[str] = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=parsed.netloc, Prefix=physical_prefix):
        for item in page.get("Contents", ()):
            key = item.get("Key") if isinstance(item, dict) else None
            if not isinstance(key, str) or not key.startswith(physical_prefix):
                continue
            relative = key.removeprefix(physical_prefix)
            if (
                not relative.startswith("session=")
                or relative.count("/") != 1
                or not relative.endswith("/manifest.json")
            ):
                continue
            _manifest_location(f"{logical_prefix}{relative}")
            manifests.add(f"s3://{parsed.netloc}/{key}")
    return tuple(sorted(manifests))


class _ReaderStore(Protocol):
    def uri(self, key: str) -> str: ...

    def read_capture(self, key: str) -> bytes | None: ...


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StreamBatch(_StrictModel):
    batch_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    first_receive_sequence: int = Field(ge=1)
    last_receive_sequence: int = Field(ge=1)
    message_count: int = Field(ge=1, le=MAX_STREAM_BATCH_MESSAGES)
    object_key: str = Field(min_length=1)
    object_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_at: datetime

    @field_validator("published_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_sequence_span(self) -> StreamBatch:
        if self.last_receive_sequence - self.first_receive_sequence + 1 != self.message_count:
            raise ValueError("batch sequence span does not match message_count")
        return self


class StreamManifest(_StrictModel):
    schema_version: Literal[1]
    stream_session_id: str = Field(min_length=1)
    symbols: tuple[str, ...]
    connected_at: datetime
    disconnected_at: datetime
    disconnect_kind: StreamDisconnectKind
    message_count: int = Field(ge=0)
    first_receive_sequence: int | None
    last_receive_sequence: int | None
    heartbeat_count: int = Field(ge=1)
    last_heartbeat_at: datetime
    last_business_message_at: datetime | None
    batch_count: int = Field(ge=0)
    batches: tuple[StreamBatch, ...]
    api_version: str
    sdk_version: str
    error_type: str | None = Field(default=None, min_length=1, max_length=128)
    published_at: datetime

    @field_validator(
        "connected_at",
        "disconnected_at",
        "last_heartbeat_at",
        "last_business_message_at",
        "published_at",
    )
    @classmethod
    def normalize_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("manifest timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_summary(self) -> StreamManifest:
        if (
            not self.symbols
            or len(self.symbols) != len(set(self.symbols))
            or any(not symbol or symbol != symbol.strip().upper() for symbol in self.symbols)
        ):
            raise ValueError("symbols must contain unique uppercase identifiers")
        if not self.connected_at <= self.disconnected_at <= self.published_at:
            raise ValueError("terminal timestamps are not ordered")
        if not self.connected_at <= self.last_heartbeat_at <= self.disconnected_at:
            raise ValueError("last heartbeat falls outside the connection")
        if self.last_business_message_at is not None and not (
            self.connected_at <= self.last_business_message_at <= self.disconnected_at
        ):
            raise ValueError("last business message falls outside the connection")
        if self.batch_count != len(self.batches):
            raise ValueError("batch_count does not match batches")
        if self.message_count == 0:
            if (
                self.first_receive_sequence is not None
                or self.last_receive_sequence is not None
                or self.last_business_message_at is not None
                or self.batches
            ):
                raise ValueError("empty session contains message lineage")
        elif (
            self.first_receive_sequence != 1
            or self.last_receive_sequence != self.message_count
            or self.last_business_message_at is None
        ):
            raise ValueError("message summary does not cover the terminal sequence")
        clean_terminal = self.disconnect_kind in {"completed", "shutdown"}
        if clean_terminal != (self.error_type is None):
            raise ValueError("terminal kind and error_type are inconsistent")
        return self


class _CapturedRow(StreamEnvelope):
    subscription_context: Literal["symbols"]
    provider_topic: None
    api_version: str
    sdk_version: str


def _manifest_location(key: str) -> tuple[date, str]:
    prefix = f"{SSI_STREAM_RAW_PREFIX}/"
    suffix = key.removeprefix(prefix)
    parts = suffix.split("/")
    if (
        not key.startswith(prefix)
        or len(parts) != 3
        or not parts[0].startswith("trade_date=")
        or not parts[1].startswith("session=")
        or parts[2] != "manifest.json"
    ):
        raise StreamCaptureReadError("unexpected SSI Stream manifest key")
    try:
        trade_date = date.fromisoformat(parts[0].removeprefix("trade_date="))
        session_id = str(UUID(parts[1].removeprefix("session=")))
    except ValueError as error:
        raise StreamCaptureReadError("unexpected SSI Stream manifest key") from error
    return trade_date, session_id


class StreamSessionReader:
    """Validate a terminal manifest once, then lazily stream its verified rows."""

    def __init__(self, store: _ReaderStore, manifest_key: str, *, prefetch: int = 8) -> None:
        if prefetch < 1:
            raise ValueError("prefetch must be positive")
        self._store = store
        self._prefetch = prefetch
        self.manifest_key = manifest_key.strip("/")
        self.trade_date, session_id = _manifest_location(self.manifest_key)
        body = store.read_capture(self.manifest_key)
        if body is None:
            raise StreamCaptureReadError("SSI Stream manifest does not exist")
        try:
            payload = json.loads(body)
            self.manifest = StreamManifest.model_validate(payload)
        except (json.JSONDecodeError, ValueError) as error:
            raise StreamCaptureReadError("invalid SSI Stream manifest") from error
        self.manifest_sha256 = sha256(body)
        if (
            self.manifest.stream_session_id != session_id
            or self.manifest.connected_at.astimezone(_MARKET_TIMEZONE).date() != self.trade_date
            or self.manifest.api_version != SSI_API_VERSION
            or self.manifest.sdk_version != SSI_SDK_VERSION
        ):
            raise StreamCaptureReadError("SSI Stream manifest lineage is inconsistent")
        self._validate_batches()

    @classmethod
    def from_uri(cls, client: Any, manifest_uri: str) -> StreamSessionReader:
        parsed = urlparse(manifest_uri)
        root, separator, relative = parsed.path.lstrip("/").partition(f"{SSI_STREAM_RAW_PREFIX}/")
        if parsed.scheme != "s3" or not parsed.netloc or not separator:
            raise StreamCaptureReadError("manifest_uri must identify an SSI Stream S3 object")
        store = S3CaptureStore(client, f"s3://{parsed.netloc}/{root.rstrip('/')}")
        return cls(store, f"{SSI_STREAM_RAW_PREFIX}/{relative}")

    @property
    def uri(self) -> str:
        return self._store.uri(self.manifest_key)

    def batch_uri(self, batch: StreamBatch) -> str:
        """Resolve one validated manifest batch through the reader-owned store."""
        return self._store.uri(batch.object_key)

    def _validate_batches(self) -> None:
        expected_sequence = 1
        observed_count = 0
        previous_published_at = self.manifest.connected_at
        session_prefix = self.manifest_key.removesuffix("/manifest.json")
        for batch in self.manifest.batches:
            expected_key = (
                f"{session_prefix}/batches/{batch.first_receive_sequence:012d}-"
                f"{batch.last_receive_sequence:012d}-{batch.object_sha256}.json.gz"
            )
            expected_id = sha256(
                canonical_json(
                    {
                        "stream_session_id": self.manifest.stream_session_id,
                        "first_receive_sequence": batch.first_receive_sequence,
                        "last_receive_sequence": batch.last_receive_sequence,
                        "object_sha256": batch.object_sha256,
                    }
                )
            )
            if (
                batch.first_receive_sequence != expected_sequence
                or batch.object_key != expected_key
                or batch.batch_id != expected_id
                or not self.manifest.connected_at
                <= batch.published_at
                <= self.manifest.published_at
                or batch.published_at < previous_published_at
            ):
                raise StreamCaptureReadError("SSI Stream batch lineage is inconsistent")
            expected_sequence = batch.last_receive_sequence + 1
            observed_count += batch.message_count
            previous_published_at = batch.published_at
        if observed_count != self.manifest.message_count:
            raise StreamCaptureReadError("SSI Stream batches do not cover message_count")

    def envelopes(self) -> Iterator[StreamEnvelope]:
        expected_sequence = 1
        last_received_at: datetime | None = None
        for batch, body in self._batch_bodies():
            try:
                lines = gzip.decompress(body).splitlines()
            except (OSError, EOFError) as error:
                raise StreamCaptureReadError("SSI Stream batch is not valid gzip") from error
            if len(lines) != batch.message_count:
                raise StreamCaptureReadError("SSI Stream batch row count is inconsistent")
            for line in lines:
                try:
                    row = _CapturedRow.model_validate_json(line)
                except ValueError as error:
                    raise StreamCaptureReadError("invalid SSI Stream captured row") from error
                if (
                    row.stream_session_id != self.manifest.stream_session_id
                    or row.receive_sequence != expected_sequence
                    or row.api_version != self.manifest.api_version
                    or row.sdk_version != self.manifest.sdk_version
                    or (row.symbol is not None and row.symbol not in self.manifest.symbols)
                    or not self.manifest.connected_at
                    <= row.received_at
                    <= self.manifest.disconnected_at
                    or (last_received_at is not None and row.received_at < last_received_at)
                    or row.received_at > batch.published_at
                    or sha256(row.message_json.encode()) != row.message_sha256
                ):
                    raise StreamCaptureReadError("SSI Stream captured row lineage is inconsistent")
                expected_sequence += 1
                last_received_at = row.received_at
                yield row
        if expected_sequence - 1 != self.manifest.message_count:
            raise StreamCaptureReadError("SSI Stream reader did not reach the terminal sequence")
        if last_received_at != self.manifest.last_business_message_at:
            raise StreamCaptureReadError("SSI Stream last business receipt is inconsistent")

    def _read_batch(self, batch: StreamBatch) -> bytes:
        body = self._store.read_capture(batch.object_key)
        if body is None or sha256(body) != batch.object_sha256:
            raise StreamCaptureReadError("SSI Stream batch is missing or has checksum drift")
        return body

    def _batch_bodies(self) -> Iterator[tuple[StreamBatch, bytes]]:
        """Prefetch a bounded window while yielding batches in manifest order."""
        batches = iter(self.manifest.batches)
        pending: deque[tuple[StreamBatch, Future[bytes]]] = deque()
        with ThreadPoolExecutor(max_workers=self._prefetch) as executor:
            for _ in range(self._prefetch):
                if (batch := next(batches, None)) is None:
                    break
                pending.append((batch, executor.submit(self._read_batch, batch)))
            while pending:
                batch, future = pending.popleft()
                yield batch, future.result()
                if (following := next(batches, None)) is not None:
                    pending.append((following, executor.submit(self._read_batch, following)))
