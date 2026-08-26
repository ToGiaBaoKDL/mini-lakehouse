"""Deterministic artifact serialization and crash-safe output commits."""

import gzip
import io
import os
import tarfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import zstandard

from document_ocr.identity import canonical_json_bytes, canonical_json_sha256, file_sha256
from document_ocr.protocol import (
    OCR_ARCHIVE_FILE,
    OCR_RESULT_FILE,
    OCR_RESULT_FILES,
    ArtifactFile,
    OcrDocumentManifest,
    OcrElement,
    OcrError,
    OcrJob,
    OcrOutput,
    OcrPageMarkdownBundle,
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
            raise OcrError(
                f"OCR output exceeds the {maximum_bytes}-byte limit",
                code="ocr_output_too_large",
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


def build_document_manifest(
    job: OcrJob,
    prepared: PreparedDocument,
    files: tuple[ArtifactFile, ...],
) -> OcrDocumentManifest:
    """Build and size-check the shared document lineage contract."""
    current_processing_id = prepared.processing_id(job.config_hash)
    manifest = OcrDocumentManifest(
        document_id=prepared.document_id,
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
        raise OcrError(
            f"OCR output exceeds the {job.limits.max_output_bytes}-byte limit",
            code="ocr_output_too_large",
        )
    return manifest


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


def write_result(destination: Path, result: OcrOutput) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.partial")
    temporary.unlink(missing_ok=True)
    try:
        temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def reset_output(output_directory: Path) -> None:
    """Clear only the two protocol commit files before starting."""
    output_directory.mkdir(parents=True, exist_ok=True)
    for filename in OCR_RESULT_FILES:
        (output_directory / filename).unlink(missing_ok=True)


def commit_output(
    job: OcrJob,
    prepared: PreparedDocument,
    manifest: OcrDocumentManifest | None,
    artifacts: Path,
    output_directory: Path,
) -> None:
    """Atomically publish an archive followed by its result commit marker."""
    artifacts.mkdir(exist_ok=True)
    archive = output_directory / OCR_ARCHIVE_FILE
    create_archive(artifacts, archive)
    reuse = job.reuse
    if manifest is None:
        if reuse is None:
            raise ValueError("A reused output requires previous document lineage")
        processing = reuse.processing_id
        manifest_sha256 = reuse.manifest_sha256
    else:
        processing = manifest.processing_id
        manifest_sha256 = canonical_json_sha256(manifest.model_dump(mode="json"))
    write_result(
        output_directory / OCR_RESULT_FILE,
        OcrOutput(
            job_id=job.job_id,
            created_at=datetime.now(UTC),
            archive_sha256=file_sha256(archive),
            archive_size_bytes=archive.stat().st_size,
            document_id=job.document_id,
            state="succeeded" if manifest is not None else "reused",
            pdf_sha256=prepared.pdf_sha256,
            pdf_size_bytes=prepared.pdf_size_bytes,
            page_count=prepared.page_count,
            processing_id=processing,
            manifest_sha256=manifest_sha256,
            manifest=manifest,
        ),
    )
