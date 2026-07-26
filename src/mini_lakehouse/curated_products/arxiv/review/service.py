"""Application service for read-only ArXiv OCR review."""

from __future__ import annotations

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.curated_products.arxiv.review.artifacts import (
    OcrArtifactContent,
    OcrArtifactReader,
)
from mini_lakehouse.curated_products.arxiv.review.models import (
    OcrDocumentFilter,
    OcrDocumentRun,
    OcrDocumentSummary,
    OcrPageElement,
    PublishedOcrManifest,
)
from mini_lakehouse.curated_products.arxiv.review.repository import ArxivOcrReviewRepository
from mini_lakehouse.platform.trino import TrinoExecutor
from mini_lakehouse.storage.object_store import create_object_store


class ArxivOcrReviewService:
    """Keep Streamlit independent of Trino, S3 paths, and artifact validation."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._repository = ArxivOcrReviewRepository(settings)
        self._artifacts = OcrArtifactReader(
            create_object_store(settings.storage),
            curated_uri=settings.storage.curated_uri,
        )

    def documents(self, filters: OcrDocumentFilter) -> tuple[OcrDocumentSummary, ...]:
        with TrinoExecutor(self._settings.trino) as executor:
            return self._repository.documents(executor, filters)

    def document_runs(self, arxiv_id: str) -> tuple[OcrDocumentRun, ...]:
        with TrinoExecutor(self._settings.trino) as executor:
            return self._repository.document_runs(executor, arxiv_id)

    def page_elements(
        self,
        *,
        processing_id: str,
        page_number: int,
    ) -> tuple[OcrPageElement, ...]:
        with TrinoExecutor(self._settings.trino) as executor:
            return self._repository.page_elements(
                executor,
                processing_id=processing_id,
                page_number=page_number,
            )

    def manifest(self, run: OcrDocumentRun) -> PublishedOcrManifest:
        return self._artifacts.manifest(run)

    def markdown(
        self,
        run: OcrDocumentRun,
        manifest: PublishedOcrManifest,
    ) -> str:
        return self._artifacts.markdown(run, manifest)

    def page_image(
        self,
        run: OcrDocumentRun,
        manifest: PublishedOcrManifest,
        *,
        page_number: int,
    ) -> OcrArtifactContent:
        return self._artifacts.page_image(run, manifest, page_number=page_number)
