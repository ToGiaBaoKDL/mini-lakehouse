"""Deterministic text projections of canonical OCR elements."""

from collections.abc import Sequence

from mini_lakehouse.processing.ocr.core.protocol import (
    OcrElement,
    OcrPageMarkdown,
    OcrPageMarkdownBundle,
)


def serialize_plain_text(elements: Sequence[OcrElement]) -> str:
    pages: dict[int, list[OcrElement]] = {}
    for element in elements:
        pages.setdefault(element.page_number, []).append(element)
    serialized_pages = []
    for page_number in sorted(pages):
        ordered = sorted(
            pages[page_number],
            key=lambda element: (element.reading_order, element.element_id),
        )
        serialized_pages.append(
            "\n\n".join(content for element in ordered if (content := element.text_content.strip()))
        )
    return "\n\n\f\n\n".join(serialized_pages)


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
