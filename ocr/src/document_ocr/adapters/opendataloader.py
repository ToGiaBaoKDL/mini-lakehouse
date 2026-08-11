"""OpenDataLoader SDK boundary and canonical output adapter."""

import json
import math
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from opendataloader_pdf import convert as convert_pdf

from document_ocr.errors import DocumentProcessingError
from document_ocr.identity import canonical_json_sha256
from document_ocr.protocol import (
    OcrElement,
    OcrPageMarkdown,
    OcrPageMarkdownBundle,
    OpenDataLoaderJob,
)

type LogSink = Callable[[str], None]

# These values define the adapter contract, rather than user-tunable extraction
# semantics. Keeping them here prevents YAML from advertising unsupported modes.
_PAGE_MARKER_TEMPLATE = "<!-- mini-lakehouse-page-{run_id}:%page-number% -->"
_NESTED_FIELDS = {"kids", "rows", "cells", "list items"}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


class OpenDataLoaderError(DocumentProcessingError):
    """OpenDataLoader output or execution violated the adapter contract."""


@dataclass(frozen=True, slots=True)
class OpenDataLoaderOutput:
    json_path: Path
    markdown_path: Path


def _page_marker(run_id: str, page_number: int | str) -> str:
    template = _PAGE_MARKER_TEMPLATE.format(run_id=run_id)
    return template.replace("%page-number%", str(page_number))


def run_upstream(
    job: OpenDataLoaderJob,
    pdf_path: Path,
    raw_output: Path,
    *,
    log: LogSink,
) -> OpenDataLoaderOutput:
    """Run the supported OpenDataLoader Python SDK with the pinned profile."""
    raw_output.mkdir()
    image_output = raw_output / "imgs"
    image_output.mkdir()
    log(json.dumps({"event": "opendataloader_started", "run_id": job.run_id}))
    options = job.options
    try:
        convert_pdf(
            input_path=str(pdf_path),
            output_dir=str(raw_output),
            format=["json", "markdown"],
            quiet=True,
            sanitize=options.sanitize,
            keep_line_breaks=options.keep_line_breaks,
            replace_invalid_chars=options.replace_invalid_chars,
            use_struct_tree=options.use_struct_tree,
            table_method=options.table_method,
            reading_order=options.reading_order,
            markdown_page_separator=_page_marker(job.run_id, "%page-number%"),
            markdown_with_html=True,
            image_output="external",
            image_format=options.image_format,
            image_dir=str(image_output),
            include_header_footer=options.include_header_footer,
            hybrid="off",
            threads="1",
        )
    except OSError as error:
        raise OpenDataLoaderError("opendataloader_unavailable", str(error)) from error
    except subprocess.CalledProcessError as error:
        raise OpenDataLoaderError(
            "opendataloader_failed",
            f"OpenDataLoader exited with status {error.returncode}",
        ) from error
    return OpenDataLoaderOutput(
        json_path=_single_output(raw_output, "*.json", "JSON"),
        markdown_path=_single_output(raw_output, "*.md", "Markdown"),
    )


def _single_output(root: Path, pattern: str, label: str) -> Path:
    candidates = sorted(root.glob(pattern))
    if len(candidates) != 1:
        raise OpenDataLoaderError(
            "invalid_opendataloader_output",
            f"Expected one {label} output, found {len(candidates)}",
        )
    return candidates[0]


def _read_utf8(path: Path, max_bytes: int) -> str:
    try:
        if path.stat().st_size > max_bytes:
            raise OpenDataLoaderError(
                "ocr_output_too_large",
                f"OpenDataLoader output exceeds the {max_bytes}-byte limit",
            )
        return path.read_text(encoding="utf-8")
    except OpenDataLoaderError:
        raise
    except (OSError, UnicodeDecodeError) as error:
        raise OpenDataLoaderError("invalid_opendataloader_output", str(error)) from error


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OpenDataLoaderError("invalid_opendataloader_output", f"{context} is not an object")
    return value


def _children(node: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    for field in ("kids", "list items", "rows", "cells"):
        value = node.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            raise OpenDataLoaderError(
                "invalid_opendataloader_output",
                f"{field!r} must be an array",
            )
        for index, child in enumerate(value):
            yield _object(child, f"{field}[{index}]")


def _text(node: Mapping[str, Any]) -> str:
    content = node.get("content")
    own = content if isinstance(content, str) else ""
    parts = [part.strip() for part in (own, *(_text(child) for child in _children(node)))]
    return "\n".join(part for part in parts if part)


def _bbox(
    node: Mapping[str, Any],
    *,
    page_number: int,
    page_sizes: tuple[tuple[float, float], ...],
) -> tuple[float, float, float, float]:
    value = node.get("bounding box")
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(
            isinstance(item, bool) or not isinstance(item, int | float) or not math.isfinite(item)
            for item in value
        )
    ):
        raise OpenDataLoaderError(
            "invalid_opendataloader_output",
            f"Page {page_number} element has an invalid bounding box",
        )
    left, bottom, right, top = (float(item) for item in value)
    width, height = page_sizes[page_number - 1]
    tolerance = 5.0
    if (
        left > right
        or bottom > top
        or left < -tolerance
        or bottom < -tolerance
        or right > width + tolerance
        or top > height + tolerance
    ):
        raise OpenDataLoaderError(
            "invalid_opendataloader_output",
            f"Page {page_number} element bounding box is outside the page",
        )
    return left, bottom, right, top


def move_images(raw_output: Path, artifacts: Path) -> None:
    """Move the SDK-owned image directory into the protocol artifact root."""
    source = raw_output / "imgs"
    if not source.exists():
        return
    if not source.is_dir() or source.is_symlink():
        raise OpenDataLoaderError("invalid_opendataloader_output", "SDK image output is invalid")
    if any(
        not path.is_file() or path.is_symlink() or path.suffix.lower() not in _IMAGE_SUFFIXES
        for path in source.iterdir()
    ):
        raise OpenDataLoaderError(
            "invalid_opendataloader_output",
            "SDK image output contains an unsupported entry",
        )
    source.replace(artifacts / "imgs")


def load_document(
    path: Path,
    *,
    expected_pages: int,
    max_bytes: int,
) -> list[dict[str, Any]]:
    try:
        payload = json.loads(_read_utf8(path, max_bytes))
    except json.JSONDecodeError as error:
        raise OpenDataLoaderError("invalid_opendataloader_output", str(error)) from error
    root = _object(payload, "OpenDataLoader document")
    page_count = root.get("number of pages")
    if page_count != expected_pages:
        raise OpenDataLoaderError(
            "incomplete_opendataloader_output",
            f"OpenDataLoader returned {page_count!r} pages; expected {expected_pages}",
        )
    kids = root.get("kids")
    if not isinstance(kids, list):
        raise OpenDataLoaderError("invalid_opendataloader_output", "Root kids must be an array")
    return [_object(node, f"kids[{index}]") for index, node in enumerate(kids)]


def load_page_markdown(
    path: Path,
    *,
    expected_pages: int,
    run_id: str,
    max_bytes: int,
) -> OcrPageMarkdownBundle:
    """Split official upstream Markdown into pages without re-rendering content."""
    markdown = _read_utf8(path, max_bytes)
    offsets: list[tuple[int, int]] = []
    for page_number in range(1, expected_pages + 1):
        marker = _page_marker(run_id, page_number)
        if markdown.count(marker) != 1:
            raise OpenDataLoaderError(
                "invalid_opendataloader_output",
                f"Expected one Markdown marker for page {page_number}",
            )
        start = markdown.index(marker)
        offsets.append((start, start + len(marker)))
    if offsets != sorted(offsets) or offsets[0][0] != 0:
        raise OpenDataLoaderError(
            "invalid_opendataloader_output", "Markdown page markers are out of order"
        )

    pages: list[OcrPageMarkdown] = []
    for page_number, (_, content_start) in enumerate(offsets, start=1):
        content_end = offsets[page_number][0] if page_number < expected_pages else len(markdown)
        content = markdown[content_start:content_end]
        if content.startswith("\r\n\r\n"):
            content = content[4:]
        elif content.startswith("\n\n"):
            content = content[2:]
        else:
            raise OpenDataLoaderError(
                "invalid_opendataloader_output",
                f"Markdown marker for page {page_number} has no content separator",
            )
        pages.append(OcrPageMarkdown(page_number=page_number, markdown=content.rstrip()))
    return OcrPageMarkdownBundle(pages=tuple(pages))


def canonical_elements(
    nodes: list[dict[str, Any]],
    *,
    current_processing_id: str,
    page_sizes: tuple[tuple[float, float], ...],
) -> tuple[OcrElement, ...]:
    """Map top-level JSON blocks without duplicating upstream Markdown rendering."""
    by_page: dict[int, list[dict[str, Any]]] = {page: [] for page in range(1, len(page_sizes) + 1)}
    for node in nodes:
        page_number = node.get("page number")
        if (
            not isinstance(page_number, int)
            or isinstance(page_number, bool)
            or page_number not in by_page
        ):
            raise OpenDataLoaderError(
                "invalid_opendataloader_output", "Top-level element has an invalid page number"
            )
        by_page[page_number].append(node)

    elements: list[OcrElement] = []
    for page_number, page_nodes in by_page.items():
        for reading_order, node in enumerate(page_nodes):
            element_type = node.get("type")
            if not isinstance(element_type, str) or not element_type:
                raise OpenDataLoaderError(
                    "invalid_opendataloader_output", "Element type must be non-empty text"
                )
            bbox = _bbox(node, page_number=page_number, page_sizes=page_sizes)
            attributes = {
                key: value
                for key, value in sorted(node.items())
                if key
                not in {
                    "bounding box",
                    "content",
                    "data",
                    "page number",
                    "source",
                    "type",
                    *_NESTED_FIELDS,
                }
            }
            source = node.get("source")
            if source is not None:
                path = PurePosixPath(source) if isinstance(source, str) else None
                if (
                    path is None
                    or source != path.as_posix()
                    or len(path.parts) != 2
                    or path.parts[0] != "imgs"
                    or path.suffix.lower() not in _IMAGE_SUFFIXES
                ):
                    raise OpenDataLoaderError(
                        "invalid_opendataloader_output",
                        f"Invalid SDK image source: {source!r}",
                    )
                attributes["asset_path"] = source
            elements.append(
                OcrElement(
                    element_id=canonical_json_sha256(
                        {
                            "page_number": page_number,
                            "processing_id": current_processing_id,
                            "reading_order": reading_order,
                        }
                    ),
                    page_number=page_number,
                    reading_order=reading_order,
                    element_type=element_type,
                    bbox_json=json.dumps(bbox, separators=(",", ":")),
                    text_content=_text(node),
                    markdown_content=None,
                    raw_attributes_json=(
                        json.dumps(
                            attributes,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        if attributes
                        else None
                    ),
                )
            )
    visible_text = "".join(element.text_content.strip() for element in elements)
    if len(visible_text) < 32:
        raise OpenDataLoaderError(
            "native_text_unavailable",
            "OpenDataLoader found too little native text; use a GLM-OCR pipeline",
        )
    return tuple(elements)
