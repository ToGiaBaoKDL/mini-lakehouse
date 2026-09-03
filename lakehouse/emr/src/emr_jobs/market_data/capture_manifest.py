"""Shared validation primitives for immutable SSI capture manifests."""

import hashlib
import json
from datetime import datetime
from typing import Any

API_VERSION = "v3"
SDK_VERSION = "3.2.1"


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def json_object(body: bytes, name: str) -> dict[str, Any]:
    value = json.loads(body)
    if not isinstance(value, dict):
        raise RuntimeError(f"Capture manifest must be an object: {name}")
    return value


def required(value: dict[str, Any], key: str, expected: type) -> Any:
    item = value.get(key)
    if not isinstance(item, expected) or (expected is int and isinstance(item, bool)):
        raise RuntimeError(f"Invalid capture manifest field: {key}")
    return item


def timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise RuntimeError(f"Invalid capture timestamp: {field}")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise RuntimeError(f"Capture timestamp must include an offset: {field}")
    return parsed


def count(value: dict[str, Any], field: str) -> int:
    result = required(value, field, int)
    if result < 0:
        raise RuntimeError(f"Invalid negative capture count: {field}")
    return result


def physical_key(base_prefix: str, logical_key: str, raw_prefix: str) -> str:
    if not logical_key.startswith(raw_prefix) or ".." in logical_key.split("/"):
        raise RuntimeError("Capture object escaped its source-owned prefix")
    return f"{base_prefix}{logical_key}"


def capture_key(manifest_key: str, raw_object_prefix: str) -> tuple[str, str]:
    """Split an S3 key into its deployment root and source-owned logical key."""
    raw_prefix = f"{raw_object_prefix.strip('/')}/"
    if manifest_key.startswith(raw_prefix):
        return "", manifest_key
    base_prefix, separator, suffix = manifest_key.rpartition(f"/{raw_prefix}")
    if not separator:
        raise RuntimeError("Unexpected SSI capture manifest URI")
    return f"{base_prefix}/", f"{raw_prefix}{suffix}"
