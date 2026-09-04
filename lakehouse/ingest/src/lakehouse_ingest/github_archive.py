"""Capture one complete GitHub Archive UTC day into immutable S3 objects."""

import gzip
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from tempfile import TemporaryFile
from typing import Any

from lakehouse.contracts.captures import GitHubArchiveManifest, GitHubArchiveObject
from loguru import logger

from lakehouse_ingest.http import session
from lakehouse_ingest.storage import S3CaptureStore

ARCHIVE_URL = "https://data.gharchive.org"
RAW_PREFIX = "api/github_archive/raw"


def _object_key(source_date: date, hour: int) -> str:
    return (
        f"{RAW_PREFIX}/source_date={source_date.isoformat()}/hour={hour:02d}/"
        f"{source_date.isoformat()}-{hour}.json.gz"
    )


def is_captured(metadata: dict[str, Any] | None) -> bool:
    if metadata is None or int(metadata["ContentLength"]) < 1:
        return False
    checksum = metadata.get("Metadata", {}).get("sha256")
    return (
        isinstance(checksum, str)
        and len(checksum) == 64
        and all(character in "0123456789abcdef" for character in checksum)
    )


def require_available(source_date: date, hours: list[int]) -> None:
    """Fail before large transfers when any required archive is unpublished."""
    if not hours:
        return
    with session() as http:
        for hour in reversed(hours):
            url = f"{ARCHIVE_URL}/{source_date.isoformat()}-{hour}.json.gz"
            with http.head(url, allow_redirects=True, timeout=(10, 30)) as response:
                if response.status_code == 404:
                    raise RuntimeError(
                        f"GitHub Archive day {source_date} is incomplete: "
                        f"hour {hour:02d} is not published"
                    )
                response.raise_for_status()


def _download(
    store: S3CaptureStore,
    key: str,
    source_date: date,
    hour: int,
) -> dict[str, Any]:
    url = f"{ARCHIVE_URL}/{source_date.isoformat()}-{hour}.json.gz"
    with session() as http, http.get(url, timeout=(10, 180), stream=True) as response:
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
                raise RuntimeError(f"GitHub Archive returned invalid gzip: {url}") from error

            artifact.seek(0)
            return store.put_file(
                key,
                artifact,
                size_bytes=size_bytes,
                sha256=digest.hexdigest(),
            )


def capture_day(
    store: S3CaptureStore,
    source_date: date,
    *,
    workers: int = 8,
) -> str:
    """Publish one complete day and return its terminal manifest URI."""
    objects = {
        hour: (key, store.head(key))
        for hour in range(24)
        for key in (_object_key(source_date, hour),)
    }
    missing = [hour for hour, (_, metadata) in objects.items() if not is_captured(metadata)]
    require_available(source_date, missing)

    def capture_hour(hour: int) -> GitHubArchiveObject:
        key, metadata = objects[hour]
        if not is_captured(metadata):
            metadata = _download(store, key, source_date, hour)
            logger.info("Captured GitHub Archive hour {:02d}", hour)
        if metadata is None:
            raise RuntimeError(f"S3 returned no metadata for {key}")
        last_modified = metadata.get("LastModified")
        if not isinstance(last_modified, datetime):
            raise RuntimeError(f"S3 returned no LastModified for {key}")
        return GitHubArchiveObject(
            hour=hour,
            key=key,
            size_bytes=metadata["ContentLength"],
            sha256=metadata["Metadata"]["sha256"],
            last_modified=last_modified.astimezone(UTC),
        )

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="github-capture") as pool:
        captured = tuple(pool.map(capture_hour, range(24)))

    manifest = GitHubArchiveManifest(source_date=source_date, objects=captured)
    manifest_key = f"{RAW_PREFIX}/source_date={source_date.isoformat()}/manifest.json"
    return store.put_manifest(manifest_key, manifest.model_dump_json().encode())
