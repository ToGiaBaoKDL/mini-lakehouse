"""Validate a terminal GitHub Archive capture manifest and its S3 lineage."""

import hashlib
from datetime import date, datetime

from lakehouse.contracts.captures import GitHubArchiveManifest

from emr_jobs.common.s3 import head_object, join_key, read_bytes, split_uri


def load_capture(
    uri: str,
    *,
    expected_source_date: date,
    raw_object_prefix: str,
) -> list[tuple[str, datetime]]:
    bucket, manifest_key = split_uri(uri)
    logical_manifest_key = (
        f"{raw_object_prefix}/source_date={expected_source_date.isoformat()}/manifest.json"
    )
    if manifest_key == logical_manifest_key:
        base_prefix = ""
    elif manifest_key.endswith(f"/{logical_manifest_key}"):
        base_prefix = manifest_key.removesuffix(logical_manifest_key)
    else:
        raise RuntimeError("Unexpected GitHub Archive capture manifest URI")

    body = read_bytes(uri)
    manifest_metadata = head_object(bucket, manifest_key)
    if (
        manifest_metadata is None
        or manifest_metadata.get("Metadata", {}).get("sha256") != hashlib.sha256(body).hexdigest()
    ):
        raise RuntimeError("GitHub Archive capture manifest checksum mismatch")
    manifest = GitHubArchiveManifest.model_validate_json(body)
    if manifest.source_date != expected_source_date:
        raise RuntimeError("GitHub Archive manifest does not match the requested source date")

    captures: list[tuple[str, datetime]] = []
    for item in manifest.objects:
        expected_key = (
            f"{raw_object_prefix}/source_date={expected_source_date.isoformat()}/"
            f"hour={item.hour:02d}/{expected_source_date.isoformat()}-{item.hour}.json.gz"
        )
        if item.key != expected_key:
            raise RuntimeError("GitHub Archive object escaped its source-date partition")
        physical_key = join_key(base_prefix, item.key)
        metadata = head_object(bucket, physical_key)
        if (
            metadata is None
            or metadata.get("ContentLength") != item.size_bytes
            or metadata.get("Metadata", {}).get("sha256") != item.sha256
        ):
            raise RuntimeError(f"GitHub Archive object checksum mismatch: {item.key}")
        captures.append((f"s3://{bucket}/{physical_key}", item.last_modified))
    return captures
