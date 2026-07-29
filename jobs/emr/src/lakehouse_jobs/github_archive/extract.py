"""Capture one immutable GitHub Archive UTC day in S3."""

import gzip
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from tempfile import TemporaryFile
from typing import Any

from loguru import logger

from lakehouse_jobs.common.http import http_session
from lakehouse_jobs.common.s3 import client, head_object, join_key, split_uri


def has_checksum(metadata: dict[str, Any] | None) -> bool:
    if metadata is None or int(metadata["ContentLength"]) < 1:
        return False
    checksum = metadata.get("Metadata", {}).get("sha256")
    return (
        isinstance(checksum, str)
        and len(checksum) == 64
        and all(character in "0123456789abcdef" for character in checksum)
    )


def _download(bucket: str, key: str, url: str) -> dict[str, Any]:
    with (
        http_session() as http,
        http.get(
            url,
            timeout=(10, 180),
            stream=True,
        ) as response,
    ):
        response.raise_for_status()
        digest = hashlib.sha256()
        size_bytes = 0
        with TemporaryFile() as artifact:
            for chunk in response.raw.stream(1024 * 1024, decode_content=False):
                if not chunk:
                    continue
                digest.update(chunk)
                size_bytes += len(chunk)
                artifact.write(chunk)
            if size_bytes < 1:
                raise RuntimeError(f"GitHub Archive returned an empty object: {url}")

            artifact.seek(0)
            try:
                with gzip.GzipFile(fileobj=artifact) as archive:
                    while archive.read(1024 * 1024):
                        pass
            except (EOFError, OSError) as error:
                raise RuntimeError(
                    f"GitHub Archive returned an invalid gzip object: {url}"
                ) from error

            artifact.seek(0)
            client().upload_fileobj(
                artifact,
                bucket,
                key,
                ExtraArgs={
                    "ContentType": "application/gzip",
                    "Metadata": {"sha256": digest.hexdigest()},
                },
            )
    return client().head_object(Bucket=bucket, Key=key)


def capture_day(
    *,
    source_date: str,
    landing_uri: str,
    raw_object_prefix: str,
    workers: int,
) -> list[tuple[str, datetime]]:
    bucket, root = split_uri(landing_uri)

    def capture_hour(hour: int) -> tuple[str, datetime]:
        filename = f"{source_date}-{hour}.json.gz"
        key = join_key(
            root,
            raw_object_prefix,
            f"source_date={source_date}",
            f"hour={hour:02d}",
            filename,
        )
        metadata = head_object(bucket, key)
        if not has_checksum(metadata):
            metadata = _download(
                bucket,
                key,
                f"https://data.gharchive.org/{filename}",
            )
            logger.info("Captured GitHub Archive hour {:02d}", hour)
        last_modified = metadata.get("LastModified") if metadata else None
        if not isinstance(last_modified, datetime):
            raise RuntimeError(f"S3 returned no LastModified for s3://{bucket}/{key}")
        return f"s3://{bucket}/{key}", last_modified.astimezone(UTC)

    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="github-capture",
    ) as pool:
        return list(pool.map(capture_hour, range(24)))
