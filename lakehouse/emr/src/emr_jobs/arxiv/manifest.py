"""Validate and load one terminal ArXiv OAI capture manifest."""

import gzip
import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime

from lakehouse.contracts.captures import ArxivOaiManifest

from emr_jobs.common.s3 import head_object, join_key, read_bytes, split_uri


@dataclass(frozen=True)
class ArxivCapture:
    pages: list[bytes]
    page_objects: list[tuple[str, str]]
    manifest_key: str
    manifest_sha256: str
    published_at: datetime


def load_capture(
    uri: str,
    *,
    expected_source_date: date,
    raw_object_prefix: str,
) -> ArxivCapture:
    bucket, manifest_key = split_uri(uri)
    day_prefix = f"{raw_object_prefix}/datestamp={expected_source_date.isoformat()}"
    logical_manifest_key = f"{day_prefix}/manifest.json"
    if manifest_key == logical_manifest_key:
        base_prefix = ""
    elif manifest_key.endswith(f"/{logical_manifest_key}"):
        base_prefix = manifest_key.removesuffix(logical_manifest_key)
    else:
        raise RuntimeError("Unexpected ArXiv OAI capture manifest URI")

    body = read_bytes(uri)
    metadata = head_object(bucket, manifest_key)
    manifest_sha256 = hashlib.sha256(body).hexdigest()
    if metadata is None or metadata.get("Metadata", {}).get("sha256") != manifest_sha256:
        raise RuntimeError("ArXiv OAI capture manifest checksum mismatch")
    published_at = metadata.get("LastModified")
    if not isinstance(published_at, datetime):
        raise RuntimeError("ArXiv OAI capture manifest has no publication timestamp")

    manifest = ArxivOaiManifest.model_validate_json(body)
    if manifest.source_date != expected_source_date:
        raise RuntimeError("ArXiv OAI manifest does not match its source-date snapshot")

    pages: list[bytes] = []
    page_objects: list[tuple[str, str]] = []
    for item in manifest.pages:
        expected_key = f"{day_prefix}/snapshot={manifest.snapshot}/page-{item.page:06d}.xml.gz"
        if item.key != expected_key:
            raise RuntimeError("ArXiv OAI page escaped its immutable snapshot")
        physical_key = join_key(base_prefix, item.key)
        page_metadata = head_object(bucket, physical_key)
        if (
            page_metadata is None
            or page_metadata.get("ContentLength") != item.size_bytes
            or page_metadata.get("Metadata", {}).get("sha256") != item.sha256
        ):
            raise RuntimeError(f"ArXiv OAI page checksum mismatch: {item.key}")
        compressed = read_bytes(f"s3://{bucket}/{physical_key}")
        if hashlib.sha256(compressed).hexdigest() != item.sha256:
            raise RuntimeError(f"ArXiv OAI page content drift: {item.key}")
        pages.append(gzip.decompress(compressed))
        page_objects.append((physical_key, item.sha256))

    return ArxivCapture(
        pages=pages,
        page_objects=page_objects,
        manifest_key=manifest_key,
        manifest_sha256=manifest_sha256,
        published_at=published_at.astimezone(UTC),
    )
