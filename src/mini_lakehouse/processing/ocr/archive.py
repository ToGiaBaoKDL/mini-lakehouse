"""Validation and safe extraction of a completed OCR runner output."""

from __future__ import annotations

import gzip
import tarfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

import zstandard

from mini_lakehouse.processing.ocr.core.files import file_sha256
from mini_lakehouse.processing.ocr.core.identity import (
    canonical_json_sha256,
    processing_id,
    successful_document_manifest_payload,
)
from mini_lakehouse.processing.ocr.core.paths import runner_document_path
from mini_lakehouse.processing.ocr.core.protocol import (
    OcrBatchManifest,
    OcrDocumentResult,
    OcrElement,
    OcrJob,
)


class InvalidOcrOutputError(ValueError):
    """The remote output violates the canonical protocol or archive boundary."""


class OcrBatchMismatchError(InvalidOcrOutputError):
    """The downloaded latest Kaggle output belongs to a different batch."""


def document_manifest_payload(result: OcrDocumentResult) -> dict[str, object]:
    if (
        result.state != "succeeded"
        or result.pdf_sha256 is None
        or result.pdf_size_bytes is None
        or result.page_count is None
        or result.processing_id is None
    ):
        raise InvalidOcrOutputError("Only successful OCR results have a content manifest")
    return successful_document_manifest_payload(
        arxiv_id=result.arxiv_id,
        pdf_sha256=result.pdf_sha256,
        pdf_size_bytes=result.pdf_size_bytes,
        page_count=result.page_count,
        processing_id=result.processing_id,
        files=[artifact.model_dump(mode="json") for artifact in result.files],
    )


def document_manifest_sha256(result: OcrDocumentResult) -> str:
    return canonical_json_sha256(document_manifest_payload(result))


def load_manifest(path: Path) -> OcrBatchManifest:
    try:
        return OcrBatchManifest.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as error:
        raise InvalidOcrOutputError(f"Invalid OCR result manifest: {error}") from error


def _validated_member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not name or path.as_posix() != name or ".." in path.parts:
        raise InvalidOcrOutputError(f"Unsafe archive member path: {name!r}")
    return path


def extract_archive(
    archive: Path,
    destination: Path,
    *,
    max_output_bytes: int,
    max_members: int = 50_000,
) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    extracted_bytes = 0
    seen: set[PurePosixPath] = set()
    try:
        with (
            archive.open("rb") as compressed,
            zstandard.ZstdDecompressor().stream_reader(compressed) as decompressed,
            tarfile.open(fileobj=decompressed, mode="r|") as bundle,
        ):
            for index, member in enumerate(bundle):
                if index >= max_members:
                    raise InvalidOcrOutputError("OCR archive contains too many members")
                member_path = _validated_member_path(member.name)
                if member_path in seen:
                    raise InvalidOcrOutputError(
                        f"OCR archive contains duplicate member {member.name!r}"
                    )
                seen.add(member_path)
                target = destination.joinpath(*member_path.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise InvalidOcrOutputError(
                        f"OCR archive member {member.name!r} is not a regular file"
                    )
                extracted_bytes += member.size
                if extracted_bytes > max_output_bytes:
                    raise InvalidOcrOutputError("OCR archive exceeds its extracted-size limit")
                source = bundle.extractfile(member)
                if source is None:
                    raise InvalidOcrOutputError(f"Cannot read archive member {member.name!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    remaining = member.size
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise InvalidOcrOutputError(
                                f"Archive member {member.name!r} ended early"
                            )
                        output.write(chunk)
                        remaining -= len(chunk)
    except (tarfile.TarError, zstandard.ZstdError, OSError) as error:
        raise InvalidOcrOutputError(f"Cannot extract OCR archive: {error}") from error


def validate_runner_output(
    output_directory: Path,
    extraction_directory: Path,
    *,
    job: OcrJob,
) -> OcrBatchManifest:
    manifest_path = output_directory / "result_manifest.json"
    archive_path = output_directory / "result.tar.zst"
    if not manifest_path.is_file() or not archive_path.is_file():
        raise InvalidOcrOutputError("OCR output requires result_manifest.json and result.tar.zst")
    manifest = load_manifest(manifest_path)
    if manifest.batch_id != job.batch_id:
        raise OcrBatchMismatchError("OCR output batch ID does not match the submitted job")
    if archive_path.stat().st_size != manifest.archive_size_bytes:
        raise InvalidOcrOutputError("OCR result archive size does not match its manifest")
    if file_sha256(archive_path) != manifest.archive_sha256:
        raise InvalidOcrOutputError("OCR result archive checksum does not match its manifest")

    requests = {document.request_id: document for document in job.documents}
    results = {document.request_id: document for document in manifest.documents}
    if results.keys() != requests.keys():
        raise InvalidOcrOutputError("OCR result request set does not match the submitted job")
    for request_key, result in results.items():
        if result.arxiv_id != requests[request_key].arxiv_id:
            raise InvalidOcrOutputError(f"OCR result paper does not match request {request_key}")
        if result.state == "succeeded":
            assert result.pdf_sha256 is not None
            expected_processing_id = processing_id(
                arxiv_id=result.arxiv_id,
                pdf_sha256=result.pdf_sha256,
                configuration_hash=job.config_hash,
            )
            if result.processing_id != expected_processing_id:
                raise InvalidOcrOutputError(
                    f"OCR processing identity does not match request {request_key}"
                )

    extract_archive(
        archive_path,
        extraction_directory,
        max_output_bytes=job.limits.max_output_bytes * len(job.documents),
    )
    expected_archive_paths = {
        runner_document_path(result.arxiv_id, result.request_id)
        / PurePosixPath(artifact.relative_path)
        for result in manifest.documents
        if result.state == "succeeded"
        for artifact in result.files
    }
    actual_archive_paths = {
        path.relative_to(extraction_directory)
        for path in extraction_directory.rglob("*")
        if path.is_file()
    }
    if actual_archive_paths != expected_archive_paths:
        raise InvalidOcrOutputError("OCR archive contains untracked or missing artifacts")
    for result in manifest.documents:
        _validate_document(extraction_directory, result)
    return manifest


def _validate_document(extraction_directory: Path, result: OcrDocumentResult) -> None:
    document_root = extraction_directory.joinpath(
        *runner_document_path(result.arxiv_id, result.request_id).parts
    )
    if result.state != "succeeded":
        if document_root.exists() and any(document_root.rglob("*")):
            raise InvalidOcrOutputError(
                f"Failed request {result.request_id} unexpectedly contains artifacts"
            )
        return
    if document_manifest_sha256(result) != result.manifest_sha256:
        raise InvalidOcrOutputError(
            f"Document manifest checksum is invalid for request {result.request_id}"
        )
    expected_paths = {PurePosixPath(file.relative_path) for file in result.files}
    actual_paths = {
        path.relative_to(document_root) for path in document_root.rglob("*") if path.is_file()
    }
    if actual_paths != expected_paths:
        raise InvalidOcrOutputError(
            f"Artifact set does not match manifest for request {result.request_id}"
        )
    for artifact in result.files:
        path = document_root.joinpath(*PurePosixPath(artifact.relative_path).parts)
        if path.stat().st_size != artifact.size_bytes or file_sha256(path) != artifact.sha256:
            raise InvalidOcrOutputError(
                f"Artifact checksum is invalid for request {result.request_id}: "
                f"{artifact.relative_path}"
            )


def artifact_paths(
    extraction_directory: Path,
    result: OcrDocumentResult,
) -> Iterable[tuple[Path, str]]:
    root = extraction_directory.joinpath(
        *runner_document_path(result.arxiv_id, result.request_id).parts
    )
    for artifact in result.files:
        yield root.joinpath(*PurePosixPath(artifact.relative_path).parts), artifact.relative_path


def load_elements(path: Path, *, max_uncompressed_bytes: int) -> tuple[OcrElement, ...]:
    elements: list[OcrElement] = []
    consumed = 0
    try:
        with gzip.open(path, "rb") as source:
            for line_number, line in enumerate(source, start=1):
                consumed += len(line)
                if consumed > max_uncompressed_bytes:
                    raise InvalidOcrOutputError("OCR elements exceed their uncompressed-size limit")
                try:
                    elements.append(OcrElement.model_validate_json(line))
                except ValueError as error:
                    raise InvalidOcrOutputError(
                        f"Invalid OCR element at line {line_number}: {error}"
                    ) from error
    except (gzip.BadGzipFile, OSError) as error:
        raise InvalidOcrOutputError(f"Cannot read OCR elements: {error}") from error
    element_ids = [element.element_id for element in elements]
    positions = [(element.page_number, element.reading_order) for element in elements]
    if len(element_ids) != len(set(element_ids)):
        raise InvalidOcrOutputError("OCR element IDs must be unique")
    if len(positions) != len(set(positions)):
        raise InvalidOcrOutputError("OCR page/reading-order positions must be unique")
    if not elements:
        raise InvalidOcrOutputError("Successful OCR output contains no elements")
    return tuple(elements)
