"""Validated read access to immutable OCR artifacts."""

import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath

from mini_lakehouse.curated_products.arxiv.review.models import (
    OcrDocumentRun,
    PublishedOcrManifest,
)
from mini_lakehouse.processing.ocr.core.paths import PAGE_MARKDOWN_BUNDLE_PATH
from mini_lakehouse.processing.ocr.core.protocol import (
    ArtifactFile,
    OcrPageMarkdownBundle,
)
from mini_lakehouse.processing.ocr.page_bundle import read_page_markdown_bundle
from mini_lakehouse.storage.object_store import ObjectStore

MANIFEST_MAX_BYTES = 2 * 1024 * 1024
MARKDOWN_MAX_BYTES = 64 * 1024 * 1024
IMAGE_MAX_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class OcrArtifactContent:
    data: bytes
    media_type: str
    relative_path: str


class OcrArtifactReader:
    """Resolve artifacts through a verified manifest, never through bucket listing."""

    def __init__(self, object_store: ObjectStore, *, curated_uri: str) -> None:
        self._object_store = object_store
        self._curated_root = curated_uri.rstrip("/")

    def manifest(self, run: OcrDocumentRun) -> PublishedOcrManifest:
        artifact_uri = self._validated_artifact_uri(run)
        assert run.manifest_sha256 is not None
        payload = self._object_store.read_bytes(
            f"{artifact_uri}/manifest.json",
            max_bytes=MANIFEST_MAX_BYTES,
        )
        if hashlib.sha256(payload).hexdigest() != run.manifest_sha256:
            raise RuntimeError("Published OCR manifest checksum does not match Iceberg state")
        manifest = PublishedOcrManifest.model_validate_json(payload)
        if (
            manifest.arxiv_id != run.arxiv_id
            or manifest.processing_id != run.processing_id
            or manifest.page_count != run.page_count
            or manifest.pdf_sha256 != run.pdf_sha256
        ):
            raise RuntimeError("Published OCR manifest lineage does not match Iceberg state")
        return manifest

    def page_image(
        self,
        run: OcrDocumentRun,
        manifest: PublishedOcrManifest,
        *,
        page_number: int,
    ) -> OcrArtifactContent:
        if page_number < 1 or page_number > manifest.page_count:
            raise ValueError(f"Page {page_number} is outside this document")
        stem = f"layout_vis/page-{page_number:04d}"
        candidates = tuple(
            file
            for file in manifest.files
            if PurePosixPath(file.relative_path).with_suffix("").as_posix() == stem
            and PurePosixPath(file.relative_path).suffix.lower() in {".jpg", ".jpeg", ".png"}
        )
        if len(candidates) != 1:
            raise FileNotFoundError(
                f"Expected one annotated image for page {page_number}, found {len(candidates)}"
            )
        return self._read_declared(run, candidates[0], max_bytes=IMAGE_MAX_BYTES)

    def page_markdowns(
        self,
        run: OcrDocumentRun,
        manifest: PublishedOcrManifest,
    ) -> OcrPageMarkdownBundle:
        relative_path = PAGE_MARKDOWN_BUNDLE_PATH.as_posix()
        content = self._read_declared(
            run,
            manifest.file(relative_path),
            max_bytes=MARKDOWN_MAX_BYTES,
        )
        bundle = read_page_markdown_bundle(
            BytesIO(content.data),
            max_uncompressed_bytes=MARKDOWN_MAX_BYTES,
        )
        if len(bundle.pages) != manifest.page_count:
            raise RuntimeError("OCR page Markdown count does not match its manifest")
        return bundle

    def _validated_artifact_uri(self, run: OcrDocumentRun) -> str:
        if not run.artifacts_available or run.artifact_uri is None:
            raise ValueError("OCR artifacts are available only for imported document runs")
        expected_prefix = f"{self._curated_root}/"
        if not run.artifact_uri.startswith(expected_prefix):
            raise RuntimeError("OCR artifact URI is outside the curated storage boundary")
        return run.artifact_uri.rstrip("/")

    def _read_declared(
        self,
        run: OcrDocumentRun,
        artifact: ArtifactFile,
        *,
        max_bytes: int,
    ) -> OcrArtifactContent:
        artifact_uri = self._validated_artifact_uri(run)
        payload = self._object_store.read_bytes(
            f"{artifact_uri}/{artifact.relative_path}",
            max_bytes=max_bytes,
        )
        if len(payload) != artifact.size_bytes:
            raise RuntimeError(f"OCR artifact size mismatch: {artifact.relative_path}")
        if hashlib.sha256(payload).hexdigest() != artifact.sha256:
            raise RuntimeError(f"OCR artifact checksum mismatch: {artifact.relative_path}")
        return OcrArtifactContent(
            data=payload,
            media_type=artifact.media_type,
            relative_path=artifact.relative_path,
        )
