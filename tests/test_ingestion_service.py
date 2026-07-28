import gzip
import json
from pathlib import Path
from unittest.mock import create_autospec

import pytest

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.sources.github_archive.client import GithubArchiveClient
from mini_lakehouse.sources.github_archive.models import ArchiveHour
from mini_lakehouse.sources.github_archive.repository import (
    GithubArchiveRepository,
    GithubArchiveWrite,
)
from mini_lakehouse.sources.github_archive.service import GithubArchiveIngestionService
from mini_lakehouse.storage.object_store import ObjectStore


def test_ingestion_skips_download_and_parse_when_hour_is_complete() -> None:
    settings = Settings()
    archive_hour = ArchiveHour.parse("2025-01-02T03:00:00Z")
    client = create_autospec(GithubArchiveClient, instance=True)
    object_store = create_autospec(ObjectStore, instance=True)
    repository = create_autospec(GithubArchiveRepository, instance=True)
    object_store.exists.return_value = True
    repository.hour_state.return_value = GithubArchiveWrite(
        row_count=42,
        snapshot_id=7,
        was_written=False,
    )
    service = GithubArchiveIngestionService(
        settings,
        client=client,
        object_store=object_store,
        repository=repository,
    )

    result = service.ingest(archive_hour)

    assert result.row_count == 42
    assert result.snapshot_id == 7
    assert result.was_written is False
    client.download.assert_not_called()
    object_store.download.assert_not_called()
    repository.write_hour.assert_not_called()


def test_retry_reuses_published_raw_archive_after_table_commit_failure() -> None:
    settings = Settings()
    archive_hour = ArchiveHour.parse("2025-01-02T03:00:00Z")
    client = create_autospec(GithubArchiveClient, instance=True)
    object_store = create_autospec(ObjectStore, instance=True)
    repository = create_autospec(GithubArchiveRepository, instance=True)
    raw_archive = json.dumps(
        {
            "id": "event-1",
            "type": "PushEvent",
            "actor": {"id": 1, "login": "octocat"},
            "repo": {"id": 2, "name": "octocat/example"},
            "payload": {"size": 1},
            "public": True,
            "created_at": "2025-01-02T03:10:00Z",
        }
    )

    def write_archive(_: ArchiveHour, destination: Path) -> Path:
        with gzip.open(destination, "wt", encoding="utf-8") as output:
            output.write(f"{raw_archive}\n")
        return destination

    def restore_archive(_: str, destination: Path) -> Path:
        return write_archive(archive_hour, destination)

    client.download.side_effect = write_archive
    object_store.exists.side_effect = [False, True]
    object_store.upload_if_absent.return_value = True
    object_store.download.side_effect = restore_archive
    repository.hour_state.side_effect = [None, None]
    repository.write_hour.side_effect = [
        RuntimeError("Iceberg commit failed"),
        GithubArchiveWrite(row_count=1, snapshot_id=8, was_written=True),
    ]
    service = GithubArchiveIngestionService(
        settings,
        client=client,
        object_store=object_store,
        repository=repository,
    )

    with pytest.raises(RuntimeError, match="Iceberg commit failed"):
        service.ingest(archive_hour)
    retried = service.ingest(archive_hour)

    assert retried.row_count == 1
    assert retried.snapshot_id == 8
    assert retried.was_written is True
    client.download.assert_called_once()
    object_store.upload_if_absent.assert_called_once()
    object_store.download.assert_called_once()
    assert repository.write_hour.call_count == 2
