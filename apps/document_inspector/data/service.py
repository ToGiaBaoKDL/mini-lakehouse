"""Application service for read-only ArXiv document inspection."""

from document_ocr.protocol import OcrPageMarkdownBundle

from apps.document_inspector.data.artifacts import (
    OcrArtifactContent,
    OcrArtifactReader,
    S3ObjectReader,
)
from apps.document_inspector.data.models import (
    OcrDocumentFilter,
    OcrDocumentRun,
    OcrDocumentSummary,
    OcrPageElement,
    PublishedOcrManifest,
)
from apps.document_inspector.data.repository import ArxivDocumentRepository
from lakehouse_platform.aws import get_runtime_parameter
from lakehouse_platform.config.settings import Settings


class ArxivDocumentService:
    """Keep Streamlit independent of Athena SQL, S3 paths, and artifact validation."""

    def __init__(self, settings: Settings) -> None:
        self._repository = ArxivDocumentRepository(settings)
        self._artifacts = OcrArtifactReader(
            S3ObjectReader(
                region_name=settings.aws_region,
                profile_name=settings.aws_profile,
            ),
            curated_uri=get_runtime_parameter(
                settings.environment,
                "storage/curated_uri",
            ),
        )

    def documents(self, filters: OcrDocumentFilter) -> tuple[OcrDocumentSummary, ...]:
        return self._repository.documents(filters)

    def document_runs(self, arxiv_id: str) -> tuple[OcrDocumentRun, ...]:
        return self._repository.document_runs(arxiv_id)

    def page_elements(
        self,
        *,
        processing_id: str,
        page_number: int,
    ) -> tuple[OcrPageElement, ...]:
        return self._repository.page_elements(
            processing_id=processing_id,
            page_number=page_number,
        )

    def manifest(self, run: OcrDocumentRun) -> PublishedOcrManifest:
        return self._artifacts.manifest(run)

    def page_image(
        self,
        run: OcrDocumentRun,
        manifest: PublishedOcrManifest,
        *,
        page_number: int,
    ) -> OcrArtifactContent:
        return self._artifacts.page_image(run, manifest, page_number=page_number)

    def page_markdowns(
        self,
        run: OcrDocumentRun,
        manifest: PublishedOcrManifest,
    ) -> OcrPageMarkdownBundle:
        return self._artifacts.page_markdowns(run, manifest)
