"""Validate one terminal SSI Stream capture and its immutable batches."""

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from emr_jobs.common.s3 import head_object, read_bytes, split_uri
from emr_jobs.market_data.capture_manifest import (
    API_VERSION,
    SDK_VERSION,
    canonical_json,
    capture_key,
    count,
    json_object,
    physical_key,
    required,
    sha256,
    timestamp,
)

MARKET_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class StreamBatch:
    uri: str
    batch_id: str
    first_receive_sequence: int
    last_receive_sequence: int
    message_count: int
    object_key: str
    object_sha256: str
    published_at: datetime


@dataclass(frozen=True)
class StreamCapture:
    trade_date: str
    stream_session_id: str
    symbols: tuple[str, ...]
    connected_at: datetime
    disconnected_at: datetime
    disconnect_kind: str
    message_count: int
    first_receive_sequence: int | None
    last_receive_sequence: int | None
    heartbeat_count: int
    manifest_key: str
    manifest_sha256: str
    published_at: datetime
    api_version: str
    sdk_version: str
    batches: tuple[StreamBatch, ...]


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise RuntimeError(f"Invalid capture checksum: {field}")
    return value


def _optional_timestamp(value: object, field: str) -> datetime | None:
    return None if value is None else timestamp(value, field)


def _location(
    manifest_key: str,
    raw_object_prefix: str,
) -> tuple[str, str, str, str]:
    base_prefix, logical_key = capture_key(manifest_key, raw_object_prefix)
    raw_prefix = f"{raw_object_prefix.strip('/')}/"
    suffix = logical_key.removeprefix(raw_prefix)
    parts = suffix.split("/")
    if (
        not logical_key.startswith(raw_prefix)
        or len(parts) != 3
        or not parts[0].startswith("trade_date=")
        or not parts[1].startswith("session=")
        or parts[2] != "manifest.json"
    ):
        raise RuntimeError("Unexpected SSI Stream manifest URI")
    trade_date = parts[0].removeprefix("trade_date=")
    session_id = parts[1].removeprefix("session=")
    try:
        datetime.strptime(trade_date, "%Y-%m-%d")
        if str(UUID(session_id)) != session_id:
            raise ValueError
    except ValueError as error:
        raise RuntimeError("Unexpected SSI Stream manifest URI") from error
    return base_prefix, logical_key, trade_date, session_id


def load_capture(uri: str, raw_object_prefix: str) -> StreamCapture:
    bucket, manifest_path = split_uri(uri)
    base_prefix, manifest_key, trade_date, session_id = _location(manifest_path, raw_object_prefix)
    raw_prefix = f"{raw_object_prefix.strip('/')}/"
    body = read_bytes(uri)
    manifest_sha256 = sha256(body)
    manifest_metadata = head_object(bucket, manifest_path)
    if (
        manifest_metadata is None
        or manifest_metadata.get("Metadata", {}).get("sha256") != manifest_sha256
    ):
        raise RuntimeError("SSI Stream manifest is missing or has checksum drift")
    manifest = json_object(body, uri)
    if manifest.get("schema_version") != 1:
        raise RuntimeError("Unsupported SSI Stream manifest schema")
    if manifest.get("api_version") != API_VERSION or manifest.get("sdk_version") != SDK_VERSION:
        raise RuntimeError("Unsupported SSI capture API or SDK version")
    if manifest.get("stream_session_id") != session_id:
        raise RuntimeError("SSI Stream manifest session does not match its URI")
    if (
        manifest.get("disconnect_kind") not in {"completed", "shutdown"}
        or manifest.get("error_type") is not None
    ):
        raise RuntimeError("SSI Stream session did not terminate cleanly")

    symbols = tuple(required(manifest, "symbols", list))
    if (
        not symbols
        or len(symbols) != len(set(symbols))
        or any(
            not isinstance(symbol, str) or not symbol or symbol != symbol.strip().upper()
            for symbol in symbols
        )
    ):
        raise RuntimeError("SSI Stream manifest has an invalid symbol scope")

    connected_at = timestamp(manifest.get("connected_at"), "connected_at")
    disconnected_at = timestamp(manifest.get("disconnected_at"), "disconnected_at")
    published_at = timestamp(manifest.get("published_at"), "published_at")
    last_heartbeat_at = _optional_timestamp(manifest.get("last_heartbeat_at"), "last_heartbeat_at")
    last_business_message_at = _optional_timestamp(
        manifest.get("last_business_message_at"), "last_business_message_at"
    )
    if (
        connected_at.astimezone(MARKET_TIMEZONE).date().isoformat() != trade_date
        or not connected_at <= disconnected_at <= published_at
        or last_heartbeat_at is None
        or not connected_at <= last_heartbeat_at <= disconnected_at
    ):
        raise RuntimeError("SSI Stream manifest timestamps are inconsistent")

    message_count = count(manifest, "message_count")
    heartbeat_count = count(manifest, "heartbeat_count")
    batch_count = count(manifest, "batch_count")
    if heartbeat_count < 1:
        raise RuntimeError("SSI Stream session has no verified heartbeat")
    if message_count > 0:
        first_sequence = required(manifest, "first_receive_sequence", int)
        last_sequence = required(manifest, "last_receive_sequence", int)
        if (
            first_sequence != 1
            or last_sequence - first_sequence + 1 != message_count
            or last_business_message_at is None
            or not connected_at <= last_business_message_at <= disconnected_at
        ):
            raise RuntimeError("SSI Stream sequence summary is inconsistent")
    else:
        first_sequence = None
        last_sequence = None
        if (
            manifest.get("first_receive_sequence") is not None
            or manifest.get("last_receive_sequence") is not None
            or last_business_message_at is not None
        ):
            raise RuntimeError("Empty SSI Stream session has message lineage")

    references = required(manifest, "batches", list)
    if len(references) != batch_count:
        raise RuntimeError("SSI Stream batch count does not match its manifest")
    session_prefix = manifest_key.removesuffix("/manifest.json")
    batches: list[StreamBatch] = []
    next_sequence = 1
    observed_messages = 0
    batch_ids: set[str] = set()
    for reference in references:
        if not isinstance(reference, dict):
            raise RuntimeError("Invalid SSI Stream batch reference")
        batch_id = _digest(reference.get("batch_id"), "batch_id")
        object_sha256 = _digest(reference.get("object_sha256"), "object_sha256")
        first = count(reference, "first_receive_sequence")
        last = count(reference, "last_receive_sequence")
        messages = count(reference, "message_count")
        batch_published_at = timestamp(reference.get("published_at"), "batch.published_at")
        object_key = required(reference, "object_key", str)
        expected_key = f"{session_prefix}/batches/{first:012d}-{last:012d}-{object_sha256}.json.gz"
        expected_batch_id = sha256(
            canonical_json(
                {
                    "stream_session_id": session_id,
                    "first_receive_sequence": first,
                    "last_receive_sequence": last,
                    "object_sha256": object_sha256,
                }
            )
        )
        if (
            batch_id in batch_ids
            or batch_id != expected_batch_id
            or object_key != expected_key
            or first != next_sequence
            or last - first + 1 != messages
            or messages < 1
            or not connected_at <= batch_published_at <= published_at
        ):
            raise RuntimeError("SSI Stream batch lineage is inconsistent")
        batch_ids.add(batch_id)
        next_sequence = last + 1
        observed_messages += messages
        object_path = physical_key(base_prefix, object_key, raw_prefix)
        metadata = head_object(bucket, object_path)
        if (
            metadata is None
            or metadata.get("Metadata", {}).get("sha256") != object_sha256
            or not isinstance(metadata.get("ContentLength"), int)
            or metadata["ContentLength"] < 1
        ):
            raise RuntimeError("SSI Stream batch is missing or has checksum drift")
        batches.append(
            StreamBatch(
                uri=f"s3://{bucket}/{object_path}",
                batch_id=batch_id,
                first_receive_sequence=first,
                last_receive_sequence=last,
                message_count=messages,
                object_key=object_key,
                object_sha256=object_sha256,
                published_at=batch_published_at,
            )
        )
    if observed_messages != message_count or (
        message_count > 0 and next_sequence - 1 != last_sequence
    ):
        raise RuntimeError("SSI Stream batches do not cover the terminal sequence")

    return StreamCapture(
        trade_date=trade_date,
        stream_session_id=session_id,
        symbols=symbols,
        connected_at=connected_at,
        disconnected_at=disconnected_at,
        disconnect_kind=required(manifest, "disconnect_kind", str),
        message_count=message_count,
        first_receive_sequence=first_sequence,
        last_receive_sequence=last_sequence,
        heartbeat_count=heartbeat_count,
        manifest_key=manifest_key,
        manifest_sha256=manifest_sha256,
        published_at=published_at,
        api_version=API_VERSION,
        sdk_version=SDK_VERSION,
        batches=tuple(batches),
    )
