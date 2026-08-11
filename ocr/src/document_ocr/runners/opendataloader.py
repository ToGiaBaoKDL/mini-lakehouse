"""Compose one native OpenDataLoader extraction and commit its output."""

import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from document_ocr.adapters.opendataloader import (
    canonical_elements,
    load_document,
    load_page_markdown,
    move_images,
    run_upstream,
)
from document_ocr.artifacts import (
    create_archive,
    describe_artifacts,
    render_canonical_layout_visualizations,
    write_elements,
    write_gzip_json,
    write_run_result,
)
from document_ocr.errors import DocumentProcessingError
from document_ocr.identity import (
    canonical_json_bytes,
    canonical_json_sha256,
    file_sha256,
    processing_id,
)
from document_ocr.output import OCR_ARCHIVE_FILE, OCR_RESULT_FILE
from document_ocr.protocol import (
    PAGE_MARKDOWN_BUNDLE_PATH,
    OcrDocumentManifest,
    OcrDocumentResult,
    OcrRunResult,
    OpenDataLoaderJob,
)
from document_ocr.source import download_pdf, pdf_page_sizes

type LogSink = Callable[[str], None]


def _reused_result(
    job: OpenDataLoaderJob,
    *,
    pdf_sha256: str,
    pdf_size_bytes: int,
    page_count: int,
) -> OcrDocumentResult | None:
    reuse = job.document.reuse
    if reuse is None or reuse.pdf_sha256 != pdf_sha256:
        return None
    if not reuse.matches(
        pdf_sha256=pdf_sha256,
        pdf_size_bytes=pdf_size_bytes,
        page_count=page_count,
    ):
        raise DocumentProcessingError(
            "reuse_lineage_mismatch",
            "Imported lineage disagrees with unchanged PDF content",
        )
    return OcrDocumentResult(
        request_id=job.document.request_id,
        document_id=job.document.document_id,
        state="reused",
        pdf_sha256=pdf_sha256,
        pdf_size_bytes=pdf_size_bytes,
        page_count=page_count,
        processing_id=reuse.processing_id,
        manifest_sha256=reuse.manifest_sha256,
    )


def _extract_document(
    job: OpenDataLoaderJob,
    *,
    pdf_path: Path,
    pdf_sha256: str,
    pdf_size_bytes: int,
    page_sizes: tuple[tuple[float, float], ...],
    temporary: Path,
    artifacts: Path,
    log: LogSink,
) -> tuple[OcrDocumentResult, OcrDocumentManifest]:
    upstream = run_upstream(
        job,
        pdf_path,
        temporary / "upstream",
        log=log,
    )
    page_count = len(page_sizes)
    nodes = load_document(
        upstream.json_path,
        expected_pages=page_count,
        max_bytes=job.limits.max_output_bytes,
    )
    move_images(upstream.json_path.parent, artifacts)
    current_processing_id = processing_id(
        document_id=job.document.document_id,
        pdf_sha256=pdf_sha256,
        configuration_hash=job.config_hash,
    )
    elements = canonical_elements(
        nodes,
        current_processing_id=current_processing_id,
        page_sizes=page_sizes,
    )
    page_markdown = load_page_markdown(
        upstream.markdown_path,
        expected_pages=page_count,
        run_id=job.run_id,
        max_bytes=job.limits.max_output_bytes,
    )
    write_gzip_json(artifacts.joinpath(*PAGE_MARKDOWN_BUNDLE_PATH.parts), page_markdown)
    write_elements(artifacts / "elements.jsonl.gz", elements)
    render_canonical_layout_visualizations(pdf_path, elements, artifacts)
    files = describe_artifacts(artifacts, job.limits.max_output_bytes)
    manifest = OcrDocumentManifest(
        document_id=job.document.document_id,
        files=files,
        page_count=page_count,
        pdf_sha256=pdf_sha256,
        pdf_size_bytes=pdf_size_bytes,
        processing_id=current_processing_id,
    )
    if (
        sum(file.size_bytes for file in files)
        + len(canonical_json_bytes(manifest.model_dump(mode="json")))
        > job.limits.max_output_bytes
    ):
        raise DocumentProcessingError(
            "ocr_output_too_large",
            f"OCR output exceeds the {job.limits.max_output_bytes}-byte limit",
        )
    result = OcrDocumentResult(
        request_id=job.document.request_id,
        document_id=job.document.document_id,
        state="succeeded",
        pdf_sha256=pdf_sha256,
        pdf_size_bytes=pdf_size_bytes,
        page_count=page_count,
        processing_id=current_processing_id,
        manifest_sha256=canonical_json_sha256(manifest.model_dump(mode="json")),
    )
    return result, manifest


def run(
    job: OpenDataLoaderJob,
    output_directory: Path,
    *,
    log: LogSink,
) -> None:
    """Execute one native extraction and atomically commit its protocol output."""
    output_directory.mkdir(parents=True, exist_ok=True)
    result_path = output_directory / OCR_RESULT_FILE
    archive_path = output_directory / OCR_ARCHIVE_FILE
    result_path.unlink(missing_ok=True)
    archive_path.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="opendataloader-") as temporary_directory:
        temporary = Path(temporary_directory)
        pdf_path = temporary / "source.pdf"
        pdf_sha256, pdf_size_bytes = download_pdf(
            job.document,
            pdf_path,
            job.limits.max_pdf_bytes,
        )
        page_sizes = pdf_page_sizes(pdf_path, job.limits.max_pages_per_document)
        page_count = len(page_sizes)
        artifacts = temporary / "artifacts"
        artifacts.mkdir()
        result = _reused_result(
            job,
            pdf_sha256=pdf_sha256,
            pdf_size_bytes=pdf_size_bytes,
            page_count=page_count,
        )
        manifest: OcrDocumentManifest | None = None
        if result is None:
            result, manifest = _extract_document(
                job,
                pdf_path=pdf_path,
                pdf_sha256=pdf_sha256,
                pdf_size_bytes=pdf_size_bytes,
                page_sizes=page_sizes,
                temporary=temporary,
                artifacts=artifacts,
                log=log,
            )

        create_archive(artifacts, archive_path)
        write_run_result(
            result_path,
            OcrRunResult(
                run_id=job.run_id,
                created_at=datetime.now(UTC),
                archive_sha256=file_sha256(archive_path),
                archive_size_bytes=archive_path.stat().st_size,
                result=result,
                document=manifest,
            ),
        )
