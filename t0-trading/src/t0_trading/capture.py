"""Immutable bounded capture of SSI REST models through the official SDK."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from botocore.exceptions import ClientError
from ssi_sdk import __version__ as SSI_SDK_VERSION

from t0_trading.evidence import public_value

API_VERSION = "v3"
SSI_REST_RAW_PREFIX = "api/ssi_fastconnect_rest/raw"


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, slots=True)
class CaptureOptions:
    trade_date: date
    job_token: str
    symbols: tuple[str, ...] = ("VIC", "VHM")
    indices: tuple[str, ...] = ("VNINDEX", "VN30")
    page_size: int = 1000
    max_pages: int = 10

    def __post_init__(self) -> None:
        if not self.job_token.strip():
            raise ValueError("job_token cannot be empty")
        if not self.symbols or not self.indices:
            raise ValueError("symbols and indices cannot be empty")
        for label, values in (("symbols", self.symbols), ("indices", self.indices)):
            if len(values) != len(set(values)) or any(
                not value or value != value.strip().upper() for value in values
            ):
                raise ValueError(f"{label} must contain unique uppercase identifiers")
        if self.page_size < 1 or self.page_size > 1000:
            raise ValueError("page_size must be between 1 and 1000")
        if self.max_pages < 1:
            raise ValueError("max_pages must be positive")


class CaptureStore(Protocol):
    def uri(self, key: str) -> str: ...

    def read_json(self, key: str) -> dict[str, Any] | None: ...

    def put_json(self, key: str, value: Mapping[str, Any]) -> tuple[str, str]: ...

    def put_capture(self, key: str, body: bytes) -> tuple[str, str]: ...


class S3CaptureStore:
    """Content-verifying immutable writes under one landing bucket."""

    def __init__(self, client: Any, landing_uri: str) -> None:
        parsed = urlparse(landing_uri.rstrip("/"))
        if parsed.scheme != "s3" or not parsed.netloc:
            raise ValueError("landing_uri must be an S3 URI")
        self._client = client
        self._bucket = parsed.netloc
        self._root = parsed.path.strip("/")

    def _physical_key(self, key: str) -> str:
        return "/".join(part for part in (self._root, key.strip("/")) if part)

    def uri(self, key: str) -> str:
        return f"s3://{self._bucket}/{self._physical_key(key)}"

    def _head(self, key: str) -> Mapping[str, Any] | None:
        try:
            return cast(
                Mapping[str, Any],
                self._client.head_object(Bucket=self._bucket, Key=self._physical_key(key)),
            )
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise

    def _put(
        self, key: str, body: bytes, *, content_type: str, content_encoding: str | None = None
    ) -> tuple[str, str]:
        digest = sha256(body)
        arguments: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": self._physical_key(key),
            "Body": body,
            "ContentType": content_type,
            "Metadata": {"sha256": digest},
            "IfNoneMatch": "*",
        }
        if content_encoding is not None:
            arguments["ContentEncoding"] = content_encoding
        try:
            self._client.put_object(**arguments)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code not in {"412", "PreconditionFailed"}:
                raise
            current = self._head(key)
            current_digest = (current or {}).get("Metadata", {}).get("sha256")
            if current_digest != digest:
                raise RuntimeError(f"Immutable capture object conflict: {key}") from error
        return key, digest

    def read_json(self, key: str) -> dict[str, Any] | None:
        current = self._head(key)
        if current is None:
            return None
        response = self._client.get_object(Bucket=self._bucket, Key=self._physical_key(key))
        body = cast(bytes, response["Body"].read())
        expected = current.get("Metadata", {}).get("sha256")
        if expected != sha256(body):
            raise RuntimeError(f"Capture object checksum mismatch: {key}")
        value = json.loads(body)
        if not isinstance(value, dict):
            raise RuntimeError(f"Capture manifest must be an object: {key}")
        return cast(dict[str, Any], value)

    def put_json(self, key: str, value: Mapping[str, Any]) -> tuple[str, str]:
        return self._put(key, canonical_json(value), content_type="application/json")

    def put_capture(self, key: str, body: bytes) -> tuple[str, str]:
        return self._put(
            key,
            body,
            content_type="application/x-ndjson",
            content_encoding="gzip",
        )


@dataclass(frozen=True, slots=True)
class _Request:
    endpoint: str
    parameters: Mapping[str, object]
    load_page: Callable[[int], Sequence[object]]
    identity: str | None = None
    page_size: int | None = None
    max_pages: int = 1


def _records(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _field(value: object, name: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _source_time(value: object) -> str | None:
    for name in ("trading_time", "trading_date", "interval_time"):
        item = _field(value, name)
        if isinstance(item, str) and item:
            return item
    return None


def _capture_request(
    request: _Request,
    *,
    store: CaptureStore,
    run_prefix: str,
    job_token_sha256: str,
    clock: Callable[[], datetime],
) -> dict[str, object]:
    parameters_json = canonical_json(request.parameters)
    parameters_sha256 = sha256(parameters_json)
    request_id = sha256(
        canonical_json(
            {
                "api_version": API_VERSION,
                "endpoint": request.endpoint,
                "job_token_sha256": job_token_sha256,
                "parameters_sha256": parameters_sha256,
                "sdk_version": SSI_SDK_VERSION,
            }
        )
    )
    request_prefix = f"{run_prefix}/requests/{request_id}"
    manifest_key = f"{request_prefix}/manifest.json"
    current = store.read_json(manifest_key)
    if current is not None:
        return {
            "request_id": request_id,
            "manifest_key": manifest_key,
            "manifest_sha256": sha256(canonical_json(current)),
        }

    pages: list[dict[str, object]] = []
    record_count = 0
    request_started_at = clock().astimezone(UTC)
    request_completed_at = request_started_at
    for page in range(1, request.max_pages + 1):
        requested_at = clock().astimezone(UTC)
        values = request.load_page(page)
        received_at = clock().astimezone(UTC)
        rows: list[dict[str, object]] = []
        for record_index, value in enumerate(values):
            public = public_value(value)
            record_json = canonical_json(public).decode("utf-8")
            symbol = _field(value, "symbol")
            rows.append(
                {
                    "request_id": request_id,
                    "endpoint": request.endpoint,
                    "request_parameters_sha256": parameters_sha256,
                    "page": page,
                    "record_index": record_index,
                    "requested_at": requested_at.isoformat(),
                    "received_at": received_at.isoformat(),
                    "record_type": type(value).__name__,
                    "symbol": symbol if isinstance(symbol, str) and symbol else request.identity,
                    "source_time_text": _source_time(value),
                    "record_json": record_json,
                    "record_sha256": sha256(record_json.encode("utf-8")),
                    "api_version": API_VERSION,
                    "sdk_version": SSI_SDK_VERSION,
                }
            )
        published_at = clock().astimezone(UTC)
        request_completed_at = published_at
        page_entry: dict[str, object] = {
            "page": page,
            "record_count": len(rows),
            "requested_at": requested_at.isoformat(),
            "received_at": received_at.isoformat(),
            "published_at": published_at.isoformat(),
        }
        if rows:
            raw = b"".join(canonical_json(row) + b"\n" for row in rows)
            body = gzip.compress(raw, compresslevel=6, mtime=0)
            object_digest = sha256(body)
            object_key = f"{request_prefix}/page-{page:06d}-{object_digest}.json.gz"
            _, stored_digest = store.put_capture(object_key, body)
            if stored_digest != object_digest:
                raise RuntimeError("Capture store returned an unexpected object checksum")
            page_entry.update({"object_key": object_key, "object_sha256": object_digest})
        pages.append(page_entry)
        record_count += len(rows)
        if request.page_size is None or len(values) < request.page_size:
            break
    else:
        raise RuntimeError(f"SSI pagination exceeded max_pages: {request.endpoint}")

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "request_id": request_id,
        "endpoint": request.endpoint,
        "parameters": dict(request.parameters),
        "request_parameters_sha256": parameters_sha256,
        "requested_at": request_started_at.isoformat(),
        "completed_at": request_completed_at.isoformat(),
        "page_count": len(pages),
        "record_count": record_count,
        "capture_status": "success",
        "error_code": None,
        "api_version": API_VERSION,
        "sdk_version": SSI_SDK_VERSION,
        "pages": pages,
        "published_at": clock().astimezone(UTC).isoformat(),
    }
    _, manifest_digest = store.put_json(manifest_key, manifest)
    return {
        "request_id": request_id,
        "manifest_key": manifest_key,
        "manifest_sha256": manifest_digest,
    }


def capture_rest(
    market: Any,
    store: CaptureStore,
    options: CaptureOptions,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> str:
    """Capture one trade date and return its immutable run-manifest URI."""
    job_token_sha256 = sha256(options.job_token.encode("utf-8"))
    run_id = job_token_sha256[:32]
    run_prefix = f"{SSI_REST_RAW_PREFIX}/trade_date={options.trade_date.isoformat()}/run={run_id}"
    run_manifest_key = f"{run_prefix}/manifest.json"
    current = store.read_json(run_manifest_key)
    if current is not None:
        expected_scope = {
            "trade_date": options.trade_date.isoformat(),
            "symbols": list(options.symbols),
            "indices": list(options.indices),
            "job_token_sha256": job_token_sha256,
            "api_version": API_VERSION,
            "sdk_version": SSI_SDK_VERSION,
        }
        if any(current.get(key) != value for key, value in expected_scope.items()):
            raise RuntimeError("Existing capture manifest does not match the requested scope")
        return store.uri(run_manifest_key)

    day = options.trade_date.strftime("%Y/%m/%d")
    day_start = f"{day} 00:00:00"
    day_end = f"{day} 23:59:59"

    requests: list[_Request] = []
    for symbol in options.symbols:
        requests.extend(
            [
                _Request(
                    "get_securities_info",
                    {"symbol": symbol},
                    lambda _page, symbol=symbol: _records(market.get_securities_info(symbol)),
                    symbol,
                ),
                _Request(
                    "get_securities_summary_historical",
                    {"symbol": symbol, "from_date": day, "to_date": day},
                    lambda _page, symbol=symbol: _records(
                        market.get_securities_summary_historical(symbol, day, day)
                    ),
                    symbol,
                ),
                _Request(
                    "get_ohlc_1day_historical",
                    {
                        "symbol": symbol,
                        "from_date": day_start,
                        "to_date": day_end,
                        "page_size": options.page_size,
                    },
                    lambda page, symbol=symbol: _records(
                        market.get_ohlc_1day_historical(
                            symbol,
                            day_start,
                            day_end,
                            page=page,
                            size=options.page_size,
                        )
                    ),
                    symbol,
                    options.page_size,
                    options.max_pages,
                ),
                _Request(
                    "get_ohlc_1minute_historical",
                    {
                        "symbol": symbol,
                        "from_date": day_start,
                        "to_date": day_end,
                        "page_size": options.page_size,
                    },
                    lambda page, symbol=symbol: _records(
                        market.get_ohlc_1minute_historical(
                            symbol,
                            day_start,
                            day_end,
                            page=page,
                            size=options.page_size,
                        )
                    ),
                    symbol,
                    options.page_size,
                    options.max_pages,
                ),
            ]
        )

    requests.append(
        _Request(
            "get_master_data_historical",
            {"from_date": day, "to_date": day, "scope_symbols": list(options.symbols)},
            lambda _page: [
                item
                for item in _records(market.get_master_data_historical(day, day))
                if _field(item, "symbol") in options.symbols
            ],
        )
    )
    for index in options.indices:
        requests.append(
            _Request(
                "get_index_summary_historical",
                {"index": index, "trading_date": day},
                lambda _page, index=index: _records(
                    market.get_index_summary_historical(index, day)
                ),
                index,
            )
        )

    request_manifests = [
        _capture_request(
            request,
            store=store,
            run_prefix=run_prefix,
            job_token_sha256=job_token_sha256,
            clock=clock,
        )
        for request in requests
    ]
    run_manifest: dict[str, Any] = {
        "schema_version": 1,
        "trade_date": options.trade_date.isoformat(),
        "symbols": list(options.symbols),
        "indices": list(options.indices),
        "job_token_sha256": job_token_sha256,
        "api_version": API_VERSION,
        "sdk_version": SSI_SDK_VERSION,
        "requests": request_manifests,
        "published_at": clock().astimezone(UTC).isoformat(),
    }
    store.put_json(run_manifest_key, run_manifest)
    return store.uri(run_manifest_key)
