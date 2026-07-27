"""Bounded decoding for the canonical OCR page Markdown bundle."""

import gzip
from typing import BinaryIO

from mini_lakehouse.processing.ocr.core.protocol import OcrPageMarkdownBundle


class InvalidPageMarkdownBundleError(ValueError):
    """The compressed page Markdown artifact violates its contract."""


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
