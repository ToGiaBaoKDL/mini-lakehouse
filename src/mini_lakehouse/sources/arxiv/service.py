"""ArXiv OAI metadata ingestion use case."""

import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import PlatformContracts, load_contracts
from mini_lakehouse.sources.arxiv.archive import (
    read_response_archive,
    response_archive_sha256,
    write_response_archive,
)
from mini_lakehouse.sources.arxiv.client import ArxivOaiClient
from mini_lakehouse.sources.arxiv.models import ArxivMetadataResult, OaiDay
from mini_lakehouse.sources.arxiv.parser import parse_oai_pages
from mini_lakehouse.sources.arxiv.repository import ArxivLandingRepository
from mini_lakehouse.storage.object_store import ObjectStore, create_object_store

logger = logging.getLogger(__name__)


class ArxivMetadataService:
    def __init__(
        self,
        settings: Settings,
        *,
        client: ArxivOaiClient | None = None,
        object_store: ObjectStore | None = None,
        repository: ArxivLandingRepository | None = None,
        contracts: PlatformContracts | None = None,
    ) -> None:
        self._settings = settings
        self._contracts = contracts or load_contracts(settings.contracts_dir)
        self._source = self._contracts.source("arxiv")
        self._client = client or ArxivOaiClient(settings.arxiv)
        self._object_store = object_store or create_object_store(settings.storage)
        self._repository = repository

    def sync_day(self, day: OaiDay, *, refresh: bool = False) -> ArxivMetadataResult:
        if self._repository is not None:
            return self._sync_day(self._repository, day, refresh=refresh)
        with ArxivLandingRepository(
            self._settings,
            contracts=self._contracts,
        ) as repository:
            return self._sync_day(repository, day, refresh=refresh)

    def _sync_day(
        self,
        repository: ArxivLandingRepository,
        day: OaiDay,
        *,
        refresh: bool,
    ) -> ArxivMetadataResult:
        raw_object_key = f"{self._source.raw_object_prefix}/datestamp={day.iso}/responses.tar.zst"
        raw_uri = f"{self._settings.storage.landing_uri.rstrip('/')}/{raw_object_key}"
        existing = repository.day_state(day.value)
        expected_schema = self._source.table_schema_contract("oai_records_raw")
        if (
            existing is not None
            and (
                existing.raw_object_key != raw_object_key
                or existing.schema_version != expected_schema
            )
            and not refresh
        ):
            raise RuntimeError(
                f"ArXiv OAI day {day.iso} checkpoint drifted; run with refresh=true "
                "after reviewing the source contract change"
            )
        raw_exists = self._object_store.exists(raw_uri)
        if (
            not refresh
            and existing is not None
            and raw_exists
            and self._object_store.sha256(raw_uri) == existing.raw_object_sha256
        ):
            logger.info("ArXiv OAI day %s already has a complete checkpoint", day.iso)
            return ArxivMetadataResult(
                datestamp_date=day.value,
                raw_uri=raw_uri,
                page_count=existing.page_count,
                record_count=existing.record_count,
                records_snapshot_id=existing.records_snapshot_id,
                checkpoint_snapshot_id=existing.checkpoint_snapshot_id,
                was_written=False,
            )

        with TemporaryDirectory(prefix="arxiv-oai-") as temporary_directory:
            archive_path = Path(temporary_directory) / "responses.tar.zst"
            if not refresh and existing is None and raw_exists:
                logger.info("Reusing uncommitted ArXiv OAI archive %s", raw_uri)
                self._object_store.download(raw_uri, archive_path)
                pages = read_response_archive(archive_path)
                archive_sha256 = response_archive_sha256(archive_path)
            else:
                pages = tuple(self._client.pages(day))
                archive_sha256 = write_response_archive(pages, archive_path)
                self._object_store.upload(archive_path, raw_uri)
            records = parse_oai_pages(
                pages,
                day,
                raw_object_key=raw_object_key,
                raw_object_sha256=archive_sha256,
                contracts=self._contracts,
            )
            write = repository.publish_day(
                records,
                datestamp_date=day.value,
                raw_object_key=raw_object_key,
                raw_object_sha256=archive_sha256,
                page_count=len(pages),
            )

        return ArxivMetadataResult(
            datestamp_date=day.value,
            raw_uri=raw_uri,
            page_count=len(pages),
            record_count=records.num_rows,
            records_snapshot_id=write.records_snapshot_id,
            checkpoint_snapshot_id=write.checkpoint_snapshot_id,
            was_written=True,
        )
