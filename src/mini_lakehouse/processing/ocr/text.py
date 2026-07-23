"""Deterministic plain-text projection of canonical OCR elements."""

from collections.abc import Sequence

from mini_lakehouse.processing.ocr.protocol import OcrElement


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
