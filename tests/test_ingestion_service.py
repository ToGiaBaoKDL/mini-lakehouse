from unittest.mock import create_autospec

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
