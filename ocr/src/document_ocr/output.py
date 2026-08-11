"""Validate and materialize one committed remote OCR output."""

import gzip
import tarfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

import zstandard

from document_ocr.identity import canonical_json_bytes, file_sha256, processing_id
from document_ocr.protocol import (
    DOCUMENT_MANIFEST_PATH,
    PAGE_MARKDOWN_BUNDLE_PATH,
    DocumentJob,
    OcrDocumentManifest,
    OcrElement,
    OcrRunResult,
)
from document_ocr.text import (
    InvalidPageMarkdownBundleError,
    read_page_markdown_bundle,
)

OCR_RESULT_FILE = "result.json"
OCR_ARCHIVE_FILE = "artifacts.tar.zst"
OCR_RESULT_FILES = (OCR_RESULT_FILE, OCR_ARCHIVE_FILE)


class InvalidOcrArchiveError(ValueError):
    pass


def _gzip_reader(source: Path | BinaryIO) -> gzip.GzipFile:
    if isinstance(source, Path):
        return gzip.open(source, "rb")
    return gzip.GzipFile(fileobj=source, mode="rb")


def validate_ocr_output(
    directory: Path,
    *,
    expected_run_id: str | None = None,
) -> OcrRunResult:
    missing = sorted(name for name in OCR_RESULT_FILES if not (directory / name).is_file())
    if missing:
        raise InvalidOcrArchiveError(f"OCR output is missing: {', '.join(missing)}")
    try:
        result = OcrRunResult.model_validate_json((directory / OCR_RESULT_FILE).read_bytes())
        archive = directory / OCR_ARCHIVE_FILE
        if result.archive_size_bytes != archive.stat().st_size:
            raise ValueError("archive size does not match its result")
        if result.archive_sha256 != file_sha256(archive):
            raise ValueError("archive checksum does not match its result")
        if expected_run_id is not None and result.run_id != expected_run_id:
            raise ValueError(f"expected run {expected_run_id}, received {result.run_id}")
    except (OSError, ValueError) as error:
        raise InvalidOcrArchiveError(f"Invalid OCR output: {error}") from error
    return result


def _member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not name or path.as_posix() != name or ".." in path.parts:
        raise InvalidOcrArchiveError(f"Unsafe archive member path: {name!r}")
    return path


def _extract(
    archive: Path,
    destination: Path,
    *,
    max_bytes: int,
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
                    raise InvalidOcrArchiveError("OCR archive contains too many members")
                member_path = _member_path(member.name)
                if member_path in seen:
                    raise InvalidOcrArchiveError(
                        f"OCR archive contains duplicate member {member.name!r}"
                    )
                seen.add(member_path)
                target = destination.joinpath(*member_path.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise InvalidOcrArchiveError(
                        f"OCR archive member {member.name!r} is not a regular file"
                    )
                extracted_bytes += member.size
                if extracted_bytes > max_bytes:
                    raise InvalidOcrArchiveError("OCR archive exceeds its extracted-size limit")
                source = bundle.extractfile(member)
                if source is None:
                    raise InvalidOcrArchiveError(f"Cannot read archive member {member.name!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    remaining = member.size
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise InvalidOcrArchiveError(
                                f"Archive member {member.name!r} ended early"
                            )
                        output.write(chunk)
                        remaining -= len(chunk)
    except (tarfile.TarError, zstandard.ZstdError, OSError) as error:
        raise InvalidOcrArchiveError(f"Cannot extract OCR archive: {error}") from error


def extract_ocr_output(
    output_directory: Path,
    destination: Path,
    *,
    job: DocumentJob,
) -> OcrRunResult:
    output = validate_ocr_output(
        output_directory,
        expected_run_id=job.run_id,
    )
    request = job.document
    result = output.result
    if result.request_id != request.request_id or result.document_id != request.document_id:
        raise InvalidOcrArchiveError("OCR result does not match its job")
    if result.processing_id != processing_id(
        document_id=result.document_id,
        pdf_sha256=result.pdf_sha256,
        configuration_hash=job.config_hash,
    ):
        raise InvalidOcrArchiveError("OCR processing identity does not match its job")
    if result.state == "reused":
        reuse = request.reuse
        if reuse is None or (
            result.pdf_sha256,
            result.pdf_size_bytes,
            result.page_count,
            result.processing_id,
            result.manifest_sha256,
        ) != (
            reuse.pdf_sha256,
            reuse.pdf_size_bytes,
            reuse.page_count,
            reuse.processing_id,
            reuse.manifest_sha256,
        ):
            raise InvalidOcrArchiveError("Reused OCR lineage does not match its job")
    elif (
        result.state == "succeeded"
        and request.reuse is not None
        and result.pdf_sha256 == request.reuse.pdf_sha256
    ):
        raise InvalidOcrArchiveError("OCR reran unchanged content")

    _extract(
        output_directory / OCR_ARCHIVE_FILE,
        destination,
        max_bytes=job.limits.max_output_bytes,
    )
    document_manifest = output.document
    expected_paths = (
        {PurePosixPath(file.relative_path) for file in document_manifest.files}
        if document_manifest is not None
        else set()
    )
    actual_paths = {
        path.relative_to(destination) for path in destination.rglob("*") if path.is_file()
    }
    if actual_paths != expected_paths:
        raise InvalidOcrArchiveError("OCR archive contains untracked or missing files")
    if document_manifest is not None:
        manifest_bytes = canonical_json_bytes(document_manifest.model_dump(mode="json"))
        _validate_document(
            destination,
            document_manifest,
            manifest_bytes,
            job.limits.max_output_bytes,
        )
        destination.joinpath(*DOCUMENT_MANIFEST_PATH.parts).write_bytes(manifest_bytes)
    return output


def _validate_document(
    root: Path,
    manifest: OcrDocumentManifest,
    manifest_bytes: bytes,
    max_uncompressed_bytes: int,
) -> None:
    if (
        sum(file.size_bytes for file in manifest.files) + len(manifest_bytes)
        > max_uncompressed_bytes
    ):
        raise InvalidOcrArchiveError("OCR document exceeds its extracted-size limit")
    for artifact in manifest.files:
        path = root.joinpath(*PurePosixPath(artifact.relative_path).parts)
        try:
            valid = (
                path.stat().st_size == artifact.size_bytes and file_sha256(path) == artifact.sha256
            )
        except OSError as error:
            raise InvalidOcrArchiveError(
                f"Cannot read OCR artifact {artifact.relative_path}: {error}"
            ) from error
        if not valid:
            raise InvalidOcrArchiveError(f"Artifact checksum is invalid: {artifact.relative_path}")
    try:
        with root.joinpath(*PAGE_MARKDOWN_BUNDLE_PATH.parts).open("rb") as source:
            page_bundle = read_page_markdown_bundle(
                source,
                max_uncompressed_bytes=max_uncompressed_bytes,
            )
    except (OSError, InvalidPageMarkdownBundleError) as error:
        raise InvalidOcrArchiveError(str(error)) from error
    if len(page_bundle.pages) != manifest.page_count:
        raise InvalidOcrArchiveError("OCR page Markdown count does not match its manifest")


def read_elements(
    source: Path | BinaryIO,
    *,
    max_uncompressed_bytes: int,
    expected_page_count: int,
) -> tuple[OcrElement, ...]:
    elements: list[OcrElement] = []
    consumed = 0
    try:
        with _gzip_reader(source) as compressed:
            for line_number, line in enumerate(compressed, start=1):
                consumed += len(line)
                if consumed > max_uncompressed_bytes:
                    raise InvalidOcrArchiveError(
                        "OCR elements exceed their uncompressed-size limit"
                    )
                try:
                    elements.append(OcrElement.model_validate_json(line))
                except ValueError as error:
                    raise InvalidOcrArchiveError(
                        f"Invalid OCR element at line {line_number}: {error}"
                    ) from error
    except (gzip.BadGzipFile, OSError) as error:
        raise InvalidOcrArchiveError(f"Cannot read OCR elements: {error}") from error
    if len({element.element_id for element in elements}) != len(elements):
        raise InvalidOcrArchiveError("OCR element IDs must be unique")
    positions = {(element.page_number, element.reading_order) for element in elements}
    if len(positions) != len(elements):
        raise InvalidOcrArchiveError("OCR page/reading-order positions must be unique")
    if not elements:
        raise InvalidOcrArchiveError("Successful OCR output contains no elements")
    if any(element.page_number > expected_page_count for element in elements):
        raise InvalidOcrArchiveError("OCR element page exceeds the document page count")
    return tuple(elements)
