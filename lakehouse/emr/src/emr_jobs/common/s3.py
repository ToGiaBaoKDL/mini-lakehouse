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


def list_keys(*, bucket: str, prefix: str) -> tuple[str, ...]:
    """List every object key below a bounded prefix using the SDK paginator."""
    paginator = client().get_paginator("list_objects_v2")
    return tuple(
        item["Key"]
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix)
        for item in page.get("Contents", [])
    )
