"""Small S3 primitives backed by boto3's credential and retry providers."""

from functools import cache
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError


@cache
def client():
    return boto3.client("s3")


def split_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri.rstrip("/"))
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Expected an S3 URI, got {uri!r}")
    return parsed.netloc, parsed.path.lstrip("/")


def join_key(*parts: str) -> str:
    return "/".join(part.strip("/") for part in parts if part.strip("/"))


def read_bytes(uri: str) -> bytes:
    bucket, key = split_uri(uri)
    if not key:
        raise ValueError("S3 URI must identify an object")
    return client().get_object(Bucket=bucket, Key=key)["Body"].read()


def head_object(bucket: str, key: str):
    try:
        return client().head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def put_if_changed(
    *,
    bucket: str,
    key: str,
    body: bytes,
    sha256: str,
    content_type: str,
    content_encoding: str | None = None,
) -> None:
    current = head_object(bucket, key)
    if current and current.get("Metadata", {}).get("sha256") == sha256:
        return
    arguments = {
        "Bucket": bucket,
        "Key": key,
        "Body": body,
        "ContentType": content_type,
        "Metadata": {"sha256": sha256},
    }
    if content_encoding:
        arguments["ContentEncoding"] = content_encoding
    client().put_object(**arguments)
