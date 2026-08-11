"""Application service for read-only ArXiv document inspection."""

from document_ocr.protocol import (
    OcrDocumentManifest,
    OcrElement,
    OcrPageMarkdownBundle,
)
from lakehouse.aws import get_runtime_parameter
from lakehouse.config.settings import Settings

from apps.arxiv_inspector.data.artifacts import (
    OcrArtifactContent,
    OcrArtifactReader,
    S3ObjectReader,
)
from apps.arxiv_inspector.data.models import (
    OcrDocumentFilter,
    OcrDocumentRun,
    OcrDocumentSummary,
)
from apps.arxiv_inspector.data.repository import ArxivDocumentRepository


class ArxivDocumentService:
    """Keep Streamlit independent of Athena SQL, S3 paths, and artifact validation."""

    def __init__(self, settings: Settings) -> None:
        self._repository = ArxivDocumentRepository(settings)
        self._artifacts = OcrArtifactReader(
            S3ObjectReader(region_name=settings.aws_region),
            curated_uri=get_runtime_parameter(
                settings.environment,
                "storage/curated_uri",
            ),
        )

    def documents(self, filters: OcrDocumentFilter) -> tuple[OcrDocumentSummary, ...]:
        return self._repository.documents(filters)

    def document_run(self, arxiv_id: str) -> OcrDocumentRun | None:
        return self._repository.document_run(arxiv_id)

    def page_elements(
        self,
        *,
        processing_id: str,
        page_number: int,
    ) -> tuple[OcrElement, ...]:
        return self._repository.page_elements(
            processing_id=processing_id,
            page_number=page_number,
        )

    def manifest(self, run: OcrDocumentRun) -> OcrDocumentManifest:
        return self._artifacts.manifest(run)

    def page_image(
        self,
        run: OcrDocumentRun,
        manifest: OcrDocumentManifest,
        *,
        page_number: int,
    ) -> OcrArtifactContent:
        return self._artifacts.page_image(run, manifest, page_number=page_number)

    def page_markdowns(
        self,
        run: OcrDocumentRun,
        manifest: OcrDocumentManifest,
    ) -> OcrPageMarkdownBundle:
        return self._artifacts.page_markdowns(run, manifest)
