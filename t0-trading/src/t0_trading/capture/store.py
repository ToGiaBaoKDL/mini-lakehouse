"""Immutable object storage shared by SSI REST and stream capture."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from t0_trading.identity import canonical_json, sha256


class CaptureStoreUnavailable(RuntimeError):
    """The capture store cannot be reached after its SDK-owned retries."""


_TRANSPORT_ERRORS = (
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
_RETRYABLE_ERROR_CODES = frozenset(
    {
        "InternalError",
        "RequestTimeout",
        "RequestTimeoutException",
        "ServiceUnavailable",
        "SlowDown",
        "Throttling",
        "ThrottlingException",
    }
)


def _raise_if_unavailable(error: ClientError) -> None:
    metadata = error.response.get("ResponseMetadata", {})
    status = metadata.get("HTTPStatusCode")
    code = error.response.get("Error", {}).get("Code")
    if (
        isinstance(status, int) and (status == 408 or status == 429 or status >= 500)
    ) or code in _RETRYABLE_ERROR_CODES:
        raise CaptureStoreUnavailable("capture store is temporarily unavailable") from error


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
            _raise_if_unavailable(error)
            raise
        except _TRANSPORT_ERRORS as error:
            raise CaptureStoreUnavailable("capture store is temporarily unavailable") from error

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
                _raise_if_unavailable(error)
                raise
            current = self._head(key)
            current_digest = (current or {}).get("Metadata", {}).get("sha256")
            if current_digest != digest:
                raise RuntimeError(f"Immutable capture object conflict: {key}") from error
        except _TRANSPORT_ERRORS as error:
            raise CaptureStoreUnavailable("capture store is temporarily unavailable") from error
        return key, digest

    def read_json(self, key: str) -> dict[str, Any] | None:
        current = self._head(key)
        if current is None:
            return None
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._physical_key(key))
        except ClientError as error:
            _raise_if_unavailable(error)
            raise
        except _TRANSPORT_ERRORS as error:
            raise CaptureStoreUnavailable("capture store is temporarily unavailable") from error
        body = cast(bytes, response["Body"].read())
        expected = current.get("Metadata", {}).get("sha256")
        if expected != sha256(body):
            raise RuntimeError(f"Capture object checksum mismatch: {key}")
        value = json.loads(body)
        if not isinstance(value, dict):
            raise RuntimeError(f"Capture manifest must be an object: {key}")
        return cast(dict[str, Any], value)

    def read_capture(self, key: str) -> bytes | None:
        """Read one object and verify its capture-owned checksum metadata."""
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._physical_key(key))
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            _raise_if_unavailable(error)
            raise
        except _TRANSPORT_ERRORS as error:
            raise CaptureStoreUnavailable("capture store is temporarily unavailable") from error
        body = cast(bytes, response["Body"].read())
        expected = response.get("Metadata", {}).get("sha256")
        if expected != sha256(body):
            raise RuntimeError(f"Capture object checksum mismatch: {key}")
        return body

    def put_json(self, key: str, value: Mapping[str, Any]) -> tuple[str, str]:
        return self._put(key, canonical_json(value), content_type="application/json")

    def put_capture(self, key: str, body: bytes) -> tuple[str, str]:
        return self._put(
            key,
            body,
            content_type="application/x-ndjson",
            content_encoding="gzip",
        )
