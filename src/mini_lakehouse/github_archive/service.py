import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.github_archive.client import GithubArchiveClient
from mini_lakehouse.github_archive.models import ArchiveHour, IngestionResult
from mini_lakehouse.github_archive.parser import parse_archive
from mini_lakehouse.storage.iceberg import LandingEventsRepository
from mini_lakehouse.storage.object_store import ObjectStore, create_object_store

logger = logging.getLogger(__name__)


class GithubArchiveIngestionService:
    def __init__(
        self,
        settings: Settings,
        *,
        client: GithubArchiveClient | None = None,
        object_store: ObjectStore | None = None,
        repository: LandingEventsRepository | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or GithubArchiveClient(settings.github_archive)
        self._object_store = object_store or create_object_store(settings.storage)
        self._repository = repository or LandingEventsRepository(settings)

    def ingest(self, archive_hour: ArchiveHour) -> IngestionResult:
        raw_uri = (
            f"{self._settings.storage.landing_uri}/api/github_archive/raw/"
            f"{archive_hour.partition_path}/{archive_hour.filename}"
        )
        with TemporaryDirectory(prefix="github-archive-") as temporary_directory:
            local_path = Path(temporary_directory) / archive_hour.filename
            if self._object_store.exists(raw_uri):
                logger.info("Reusing immutable raw archive %s", raw_uri)
                self._object_store.download(raw_uri, local_path)
            else:
                logger.info("Downloading %s", archive_hour.filename)
                self._client.download(archive_hour, local_path)
                self._object_store.upload(local_path, raw_uri)

            parsed = parse_archive(
                local_path,
                archive_hour,
                max_error_ratio=self._settings.github_archive.max_parse_error_ratio,
            )
            snapshot_id = self._repository.replace_hour(parsed.table)

        result = IngestionResult(
            archive_hour=archive_hour.value,
            source_file=archive_hour.filename,
            raw_uri=raw_uri,
            row_count=parsed.table.num_rows,
            rejected_row_count=parsed.rejected_row_count,
            snapshot_id=snapshot_id,
        )
        logger.info("Ingested GitHub Archive hour: %s", result.model_dump(mode="json"))
        return result
