"""Immutable object storage shared by SSI REST and Stream capture."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from botocore.exceptions import ClientError


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
