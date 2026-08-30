"""Validate one immutable SSI REST capture manifest and its object lineage."""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from emr_jobs.common.s3 import head_object, read_bytes, split_uri

RAW_PREFIX = "api/ssi_fastconnect_rest/raw/"
API_VERSION = "v3"
SDK_VERSION = "3.2.0"


@dataclass(frozen=True)
class CaptureObject:
    uri: str
    object_key: str
    object_sha256: str
    page: int
    record_count: int
    requested_at: datetime
    received_at: datetime
    published_at: datetime


@dataclass(frozen=True)
class RequestPublication:
    request_id: str
    endpoint: str
    request_parameters_sha256: str
    requested_at: datetime
    completed_at: datetime
    page_count: int
    record_count: int
    capture_status: str
    error_code: str | None
    manifest_key: str
    manifest_sha256: str
    published_at: datetime


@dataclass(frozen=True)
class CaptureRun:
    trade_date: str
    symbols: tuple[str, ...]
    indices: tuple[str, ...]
    api_version: str
    sdk_version: str
    requests: tuple[RequestPublication, ...]
    objects: tuple[CaptureObject, ...]


def require_bounded_scope(capture: CaptureRun) -> None:
    expected = Counter(
        {
            "get_securities_info": len(capture.symbols),
            "get_securities_summary_historical": len(capture.symbols),
            "get_ohlc_1day_historical": len(capture.symbols),
            "get_ohlc_1minute_historical": len(capture.symbols),
            "get_master_data_historical": 1,
            "get_index_summary_historical": len(capture.indices),
        }
    )
    if Counter(item.endpoint for item in capture.requests) != expected:
        raise RuntimeError("SSI capture does not contain the required bounded request set")


def _json_object(body: bytes, name: str) -> dict[str, Any]:
    value = json.loads(body)
    if not isinstance(value, dict):
        raise RuntimeError(f"Capture manifest must be an object: {name}")
    return value


def _required(value: dict[str, Any], key: str, expected: type) -> Any:
    item = value.get(key)
    if not isinstance(item, expected) or (expected is int and isinstance(item, bool)):
        raise RuntimeError(f"Invalid capture manifest field: {key}")
    return item


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise RuntimeError(f"Invalid capture timestamp: {field}")
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise RuntimeError(f"Capture timestamp must include an offset: {field}")
    return timestamp


def _count(value: dict[str, Any], field: str) -> int:
    count = _required(value, field, int)
    if count < 0:
        raise RuntimeError(f"Invalid negative capture count: {field}")
    return count


def _physical_key(base_prefix: str, logical_key: str) -> str:
    if not logical_key.startswith(RAW_PREFIX) or ".." in logical_key.split("/"):
        raise RuntimeError("Capture object escaped its source-owned prefix")
    return f"{base_prefix}{logical_key}"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def load_capture(uri: str, expected_trade_date: str) -> CaptureRun:
    bucket, manifest_key = split_uri(uri)
    marker = manifest_key.find(RAW_PREFIX)
    if marker < 0 or not manifest_key.endswith("/manifest.json"):
        raise RuntimeError("Unexpected SSI capture manifest URI")
    base_prefix = manifest_key[:marker]
    logical_run_key = manifest_key[marker:]
    run_prefix = logical_run_key.removesuffix("/manifest.json")
    run = _json_object(read_bytes(uri), uri)
    if run.get("schema_version") != 1 or run.get("trade_date") != expected_trade_date:
        raise RuntimeError("SSI capture manifest does not match the requested trade date")
    if run.get("api_version") != API_VERSION or run.get("sdk_version") != SDK_VERSION:
        raise RuntimeError("Unsupported SSI capture API or SDK version")
    symbols = tuple(_required(run, "symbols", list))
    indices = tuple(_required(run, "indices", list))
    if (
        not symbols
        or not indices
        or any(
            not isinstance(item, str) or not item or item != item.strip().upper()
            for item in (*symbols, *indices)
        )
        or len(symbols) != len(set(symbols))
        or len(indices) != len(set(indices))
    ):
        raise RuntimeError("SSI capture manifest has an invalid scope")

    references = _required(run, "requests", list)
    publications: list[RequestPublication] = []
    objects: list[CaptureObject] = []
    request_ids: set[str] = set()
    for reference in references:
        if not isinstance(reference, dict):
            raise RuntimeError("Invalid SSI request-manifest reference")
        logical_manifest_key = _required(reference, "manifest_key", str)
        if not logical_manifest_key.startswith(f"{run_prefix}/requests/"):
            raise RuntimeError("SSI request manifest escaped its capture run")
        expected_sha256 = _required(reference, "manifest_sha256", str)
        request_uri = f"s3://{bucket}/{_physical_key(base_prefix, logical_manifest_key)}"
        body = read_bytes(request_uri)
        if hashlib.sha256(body).hexdigest() != expected_sha256:
            raise RuntimeError("SSI request manifest checksum mismatch")
        request = _json_object(body, request_uri)
        request_id = _required(request, "request_id", str)
        if request_id in request_ids or request_id != reference.get("request_id"):
            raise RuntimeError("Duplicate or mismatched SSI request ID")
        request_ids.add(request_id)
        if request.get("schema_version") != 1 or request.get("capture_status") != "success":
            raise RuntimeError("SSI request capture is not a successful v1 publication")
        if request.get("api_version") != API_VERSION or request.get("sdk_version") != SDK_VERSION:
            raise RuntimeError("SSI request API or SDK version does not match its run")
        parameters = _required(request, "parameters", dict)
        parameters_sha256 = _required(request, "request_parameters_sha256", str)
        if hashlib.sha256(_canonical_json(parameters)).hexdigest() != parameters_sha256:
            raise RuntimeError("SSI request parameters checksum mismatch")

        requested_at = _timestamp(request.get("requested_at"), "requested_at")
        completed_at = _timestamp(request.get("completed_at"), "completed_at")
        published_at = _timestamp(request.get("published_at"), "published_at")
        if not requested_at <= completed_at <= published_at:
            raise RuntimeError("SSI request publication timestamps are out of order")

        pages = _required(request, "pages", list)
        page_count = _count(request, "page_count")
        record_count = _count(request, "record_count")
        if page_count != len(pages):
            raise RuntimeError("SSI request page count does not match its manifest")
        observed_records = 0
        for expected_page, page in enumerate(pages, start=1):
            if (
                not isinstance(page, dict)
                or not isinstance(page.get("record_count"), int)
                or isinstance(page.get("record_count"), bool)
                or page["record_count"] < 0
                or page.get("page") != expected_page
            ):
                raise RuntimeError("Invalid SSI request page")
            page_requested_at = _timestamp(page.get("requested_at"), "page.requested_at")
            page_received_at = _timestamp(page.get("received_at"), "page.received_at")
            page_published_at = _timestamp(page.get("published_at"), "page.published_at")
            if not (
                requested_at
                <= page_requested_at
                <= page_received_at
                <= page_published_at
                <= completed_at
            ):
                raise RuntimeError("SSI capture page timestamps are out of order")
            observed_records += page["record_count"]
            if page["record_count"] == 0:
                if "object_key" in page or "object_sha256" in page:
                    raise RuntimeError("Empty SSI request page cannot reference an object")
                continue
            logical_key = _required(page, "object_key", str)
            request_prefix = logical_manifest_key.removesuffix("/manifest.json")
            if not logical_key.startswith(f"{request_prefix}/"):
                raise RuntimeError("SSI capture object escaped its request")
            object_sha256 = _required(page, "object_sha256", str)
            physical_key = _physical_key(base_prefix, logical_key)
            metadata = head_object(bucket, physical_key)
            if metadata is None or metadata.get("Metadata", {}).get("sha256") != object_sha256:
                raise RuntimeError("SSI capture object is missing or has checksum drift")
            objects.append(
                CaptureObject(
                    uri=f"s3://{bucket}/{physical_key}",
                    object_key=logical_key,
                    object_sha256=object_sha256,
                    page=expected_page,
                    record_count=page["record_count"],
                    requested_at=page_requested_at,
                    received_at=page_received_at,
                    published_at=page_published_at,
                )
            )
        if observed_records != record_count:
            raise RuntimeError("SSI request record count does not match its pages")
        publications.append(
            RequestPublication(
                request_id=request_id,
                endpoint=_required(request, "endpoint", str),
                request_parameters_sha256=parameters_sha256,
                requested_at=requested_at,
                completed_at=completed_at,
                page_count=page_count,
                record_count=record_count,
                capture_status="success",
                error_code=None,
                manifest_key=logical_manifest_key,
                manifest_sha256=expected_sha256,
                published_at=published_at,
            )
        )
    return CaptureRun(
        trade_date=expected_trade_date,
        symbols=symbols,
        indices=indices,
        api_version=API_VERSION,
        sdk_version=SDK_VERSION,
        requests=tuple(publications),
        objects=tuple(objects),
    )
