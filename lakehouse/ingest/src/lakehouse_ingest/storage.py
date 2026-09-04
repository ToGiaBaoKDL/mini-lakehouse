"""Immutable S3 storage owned by external-source captures."""

import hashlib
from pathlib import PurePosixPath
from typing import Any, BinaryIO, cast
from urllib.parse import urlparse

from botocore.exceptions import ClientError


class S3CaptureStore:
    def __init__(self, client: Any, landing_uri: str) -> None:
        parsed = urlparse(landing_uri.rstrip("/"))
        if parsed.scheme != "s3" or not parsed.netloc:
            raise ValueError("landing_uri must be an S3 URI")
        self._client = client
        self._bucket = parsed.netloc
        self._root = parsed.path.strip("/")

    def physical_key(self, key: str) -> str:
        path = PurePosixPath(key)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("capture key must be a normalized relative path")
        return "/".join(part for part in (self._root, str(path)) if part)

    def uri(self, key: str) -> str:
        return f"s3://{self._bucket}/{self.physical_key(key)}"

    def head(self, key: str) -> dict[str, Any] | None:
        try:
            return cast(
                dict[str, Any],
                self._client.head_object(Bucket=self._bucket, Key=self.physical_key(key)),
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {
                "404",
                "NoSuchKey",
                "NotFound",
            }:
                return None
            raise

    def read_manifest(self, key: str) -> bytes | None:
        metadata = self.head(key)
        if metadata is None:
            return None
        response = self._client.get_object(Bucket=self._bucket, Key=self.physical_key(key))
        stream = response["Body"]
        try:
            body = cast(bytes, stream.read())
        finally:
            stream.close()
        digest = hashlib.sha256(body).hexdigest()
        if (
            metadata.get("ContentLength") != len(body)
            or metadata.get("Metadata", {}).get("sha256") != digest
        ):
            raise RuntimeError(f"Capture manifest checksum mismatch: {key}")
        return body

    def put_file(
        self,
        key: str,
        body: BinaryIO,
        *,
        size_bytes: int,
        sha256: str,
        content_type: str = "application/gzip",
        content_encoding: str | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": self.physical_key(key),
            "Body": body,
            "ContentLength": size_bytes,
            "ContentType": content_type,
            "Metadata": {"sha256": sha256},
            "IfNoneMatch": "*",
        }
        if content_encoding is not None:
            arguments["ContentEncoding"] = content_encoding
        try:
            self._client.put_object(**arguments)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in {
                "412",
                "PreconditionFailed",
            }:
                raise
            current = self.head(key)
            if (
                current is None
                or current.get("ContentLength") != size_bytes
                or current.get("Metadata", {}).get("sha256") != sha256
            ):
                raise RuntimeError(f"Immutable capture object conflict: {key}") from error
        metadata = self.head(key)
        if metadata is None:
            raise RuntimeError(f"S3 did not persist capture object: {key}")
        return metadata

    def put_manifest(self, key: str, body: bytes) -> str:
        digest = hashlib.sha256(body).hexdigest()
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=self.physical_key(key),
                Body=body,
                ContentType="application/json",
                Metadata={"sha256": digest},
                IfNoneMatch="*",
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in {
                "412",
                "PreconditionFailed",
            }:
                raise
            if self.read_manifest(key) != body:
                raise RuntimeError(f"Immutable capture manifest conflict: {key}") from error
        return self.uri(key)
