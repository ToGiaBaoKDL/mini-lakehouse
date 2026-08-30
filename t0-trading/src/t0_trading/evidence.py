"""Bounded, credential-free representation of provider observations."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

SENSITIVE_FIELDS = frozenset(
    {
        "access_token",
        "api_key",
        "api_secret",
        "authorization",
        "client_id",
        "private_key",
        "refresh_token",
    }
)


def public_value(value: Any) -> Any:
    """Convert an SDK value to JSON data while recursively removing sensitive fields."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return public_value(value.value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return public_value(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): public_value(item)
            for key, item in value.items()
            if str(key).lower() not in SENSITIVE_FIELDS and not str(key).startswith("_")
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [public_value(item) for item in value]
    raise TypeError(f"Unsupported evidence value: {type(value).__name__}")


def fingerprint(value: Any) -> str:
    encoded = json.dumps(
        public_value(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def observation(value: Any, *, sample_limit: int = 2) -> dict[str, Any]:
    records = value if isinstance(value, list) else ([] if value is None else [value])
    public_records = [public_value(item) for item in records]
    fields: dict[str, dict[str, Any]] = {}
    for record in public_records:
        if not isinstance(record, dict):
            continue
        for name, item in record.items():
            field = fields.setdefault(name, {"types": set(), "nulls": 0})
            field["types"].add(type(item).__name__)
            field["nulls"] += item is None
            if isinstance(item, list):
                field.setdefault("list_lengths", set()).add(len(item))
    normalized_fields = {}
    for name, field in sorted(fields.items()):
        normalized_fields[name] = {
            "types": sorted(field["types"]),
            "nulls": field["nulls"],
        }
        if "list_lengths" in field:
            normalized_fields[name]["list_lengths"] = sorted(field["list_lengths"])
    fingerprints = Counter(fingerprint(item) for item in public_records)
    return {
        "count": len(public_records),
        "model": type(records[0]).__name__ if records else None,
        "fields": normalized_fields,
        "duplicate_records": sum(count - 1 for count in fingerprints.values()),
        "samples": public_records[:sample_limit],
    }
