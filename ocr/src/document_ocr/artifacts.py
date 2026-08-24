"""Deterministic artifact serialization and crash-safe output commits."""

import gzip
import io
import json
import os
import tarfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pymupdf
import zstandard

from document_ocr.identity import canonical_json_bytes, canonical_json_sha256, file_sha256
from document_ocr.protocol import (
    OCR_ARCHIVE_FILE,
    OCR_RESULT_FILE,
    OCR_RESULT_FILES,
    ArtifactFile,
    DocumentJob,
    DocumentProcessingError,
    OcrDocumentManifest,
    OcrDocumentResult,
    OcrElement,
    OcrPageMarkdownBundle,
    OcrRunResult,
)
from document_ocr.source import PreparedDocument

_MEDIA_TYPES = {
    ".gz": "application/gzip",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def write_gzip_json(path: Path, value: OcrPageMarkdownBundle) -> None:
    with (
        path.open("xb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        io.TextIOWrapper(compressed, encoding="utf-8") as output,
    ):
        output.write(value.model_dump_json())


def write_elements(path: Path, elements: Iterable[OcrElement]) -> None:
    with (
        path.open("xb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        io.TextIOWrapper(compressed, encoding="utf-8") as output,
    ):
        for element in elements:
            output.write(element.model_dump_json())
            output.write("\n")


def describe_artifacts(root: Path, maximum_bytes: int) -> tuple[ArtifactFile, ...]:
    files: list[ArtifactFile] = []
    total = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        size = path.stat().st_size
        total += size
        if total > maximum_bytes:
            raise DocumentProcessingError(
                "ocr_output_too_large",
                f"OCR output exceeds the {maximum_bytes}-byte limit",
            )
        files.append(
            ArtifactFile(
                relative_path=path.relative_to(root).as_posix(),
                sha256=file_sha256(path),
                size_bytes=size,
                media_type=_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
            )
        )
    return tuple(files)


def build_document_result(
    job: DocumentJob,
    prepared: PreparedDocument,
    files: tuple[ArtifactFile, ...],
) -> tuple[OcrDocumentResult, OcrDocumentManifest]:
    """Build and size-check the shared document lineage contract."""
    current_processing_id = prepared.processing_id(job.config_hash)
    manifest = OcrDocumentManifest(
        document_id=prepared.request.document_id,
        files=files,
        page_count=prepared.page_count,
        pdf_sha256=prepared.pdf_sha256,
        pdf_size_bytes=prepared.pdf_size_bytes,
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
        request_id=prepared.request.request_id,
        document_id=prepared.request.document_id,
        state="succeeded",
        pdf_sha256=prepared.pdf_sha256,
        pdf_size_bytes=prepared.pdf_size_bytes,
        page_count=prepared.page_count,
        processing_id=current_processing_id,
        manifest_sha256=canonical_json_sha256(manifest.model_dump(mode="json")),
    )
    return result, manifest


def render_canonical_layout_visualizations(
    pdf_path: Path,
    elements: Iterable[OcrElement],
    output: Path,
) -> None:
    """Render fallback layout images when a processor SDK does not provide them."""
    by_page: dict[int, list[OcrElement]] = {}
    for element in elements:
        by_page.setdefault(element.page_number, []).append(element)
    target = output / "layout_vis"
    target.mkdir()
    try:
        with pymupdf.open(pdf_path) as document:
            for page_index in range(document.page_count):
                page_number = page_index + 1
                page = document.load_page(page_index)
                height = float(page.rect.height)
                for element in by_page.get(page_number, []):
                    if element.bbox_json is None:
                        continue
                    left, bottom, right, top = json.loads(element.bbox_json)
                    rectangle = pymupdf.Rect(left, height - top, right, height - bottom)
                    rectangle &= page.rect
                    if not rectangle.is_empty:
                        page.draw_rect(rectangle, color=(1, 0, 0), width=0.8, overlay=True)
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
                pixmap.save(target / f"page-{page_number:04d}.jpg", jpg_quality=90)
    except Exception as error:
        raise DocumentProcessingError("visualization_failed", str(error)) from error


def create_archive(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.partial")
    temporary.unlink(missing_ok=True)
    try:
        with (
            temporary.open("xb") as raw_output,
            zstandard.ZstdCompressor(level=9, write_checksum=True).stream_writer(
                raw_output
            ) as output,
            tarfile.open(fileobj=output, mode="w|") as bundle,
        ):
            for path in sorted(item for item in source.rglob("*") if item.is_file()):
                info = tarfile.TarInfo(path.relative_to(source).as_posix())
                info.size = path.stat().st_size
                info.mode = 0o644
                info.mtime = 0
                with path.open("rb") as file:
                    bundle.addfile(info, file)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def write_run_result(destination: Path, result: OcrRunResult) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.partial")
    temporary.unlink(missing_ok=True)
    try:
        temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def reset_run_output(output_directory: Path) -> None:
    """Clear only the two protocol commit files for a fresh attempt."""
    output_directory.mkdir(parents=True, exist_ok=True)
    for filename in OCR_RESULT_FILES:
        (output_directory / filename).unlink(missing_ok=True)


def commit_run_output(
    job: DocumentJob,
    result: OcrDocumentResult,
    manifest: OcrDocumentManifest | None,
    artifacts: Path,
    output_directory: Path,
) -> None:
    """Atomically publish an archive followed by its result commit marker."""
    artifacts.mkdir(exist_ok=True)
    archive = output_directory / OCR_ARCHIVE_FILE
    create_archive(artifacts, archive)
    write_run_result(
        output_directory / OCR_RESULT_FILE,
        OcrRunResult(
            run_id=job.run_id,
            created_at=datetime.now(UTC),
            archive_sha256=file_sha256(archive),
            archive_size_bytes=archive.stat().st_size,
            result=result,
            document=manifest,
        ),
    )
