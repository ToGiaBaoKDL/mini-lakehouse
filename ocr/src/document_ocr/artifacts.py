"""Deterministic artifact serialization and crash-safe output commits."""

import gzip
import io
import json
import os
import tarfile
from collections.abc import Iterable
from pathlib import Path

import pymupdf
import zstandard

from document_ocr.errors import DocumentProcessingError
from document_ocr.identity import file_sha256
from document_ocr.protocol import (
    ArtifactFile,
    OcrElement,
    OcrPageMarkdownBundle,
    OcrRunResult,
)

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


def render_layout_visualizations(
    pdf_path: Path,
    elements: Iterable[OcrElement],
    output: Path,
) -> None:
    """Materialize one annotated page image per PDF page."""
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
