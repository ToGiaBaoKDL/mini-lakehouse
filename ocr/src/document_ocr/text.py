"""Deterministic text projections of canonical OCR elements."""

import gzip
from collections.abc import Sequence
from typing import BinaryIO

from document_ocr.protocol import (
    OcrElement,
    OcrPageMarkdown,
    OcrPageMarkdownBundle,
)


class InvalidPageMarkdownBundleError(ValueError):
    """The compressed page Markdown projection violates its contract."""


def build_page_markdown_bundle(
    elements: Sequence[OcrElement],
    *,
    page_count: int,
) -> OcrPageMarkdownBundle:
    """Build exactly one Markdown projection for every source page."""
    if page_count < 1:
        raise ValueError("page_count must be positive")
    pages: dict[int, list[OcrElement]] = {
        page_number: [] for page_number in range(1, page_count + 1)
    }
    for element in elements:
        try:
            pages[element.page_number].append(element)
        except KeyError as error:
            raise ValueError(
                f"OCR element page {element.page_number} exceeds page_count {page_count}"
            ) from error
    return OcrPageMarkdownBundle(
        schema_version="1.0.0",
        pages=tuple(
            OcrPageMarkdown(
                page_number=page_number,
                markdown="\n\n".join(
                    element.markdown_content
                    for element in sorted(
                        pages[page_number],
                        key=lambda item: (item.reading_order, item.element_id),
                    )
                    if element.markdown_content
                ),
            )
            for page_number in range(1, page_count + 1)
        ),
    )


def read_page_markdown_bundle(
    source: BinaryIO,
    *,
    max_uncompressed_bytes: int,
) -> OcrPageMarkdownBundle:
    try:
        with gzip.GzipFile(fileobj=source, mode="rb") as compressed:
            payload = compressed.read(max_uncompressed_bytes + 1)
    except (EOFError, gzip.BadGzipFile, OSError) as error:
        raise InvalidPageMarkdownBundleError(
            f"Cannot decompress OCR page Markdown: {error}"
        ) from error
    if len(payload) > max_uncompressed_bytes:
        raise InvalidPageMarkdownBundleError(
            "OCR page Markdown exceeds its uncompressed-size limit"
        )
    try:
        return OcrPageMarkdownBundle.model_validate_json(payload)
    except ValueError as error:
        raise InvalidPageMarkdownBundleError(
            f"Invalid OCR page Markdown bundle: {error}"
        ) from error
