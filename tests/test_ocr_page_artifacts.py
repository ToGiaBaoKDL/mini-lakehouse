import gzip
from io import BytesIO

import pytest
from document_ocr.protocol import (
    ArtifactFile,
    OcrDocumentResult,
    OcrElement,
    OcrPageMarkdown,
    OcrPageMarkdownBundle,
)
from document_ocr.text import (
    InvalidPageMarkdownBundleError,
    build_page_markdown_bundle,
    read_page_markdown_bundle,
)


def _artifact(relative_path: str, marker: str) -> ArtifactFile:
    return ArtifactFile(
        relative_path=relative_path,
        sha256=marker * 64,
        size_bytes=1,
        media_type="application/octet-stream",
    )


def test_page_markdown_preserves_sdk_page_boundaries_and_block_content() -> None:
    elements = (
        OcrElement(
            element_id="1" * 64,
            page_number=1,
            reading_order=0,
            element_type="title",
            text_content="First",
            markdown_content="# First",
        ),
        OcrElement(
            element_id="2" * 64,
            page_number=1,
            reading_order=1,
            element_type="text",
            text_content="Contains an in-page rule",
            markdown_content="Contains\n\n---\n\nan in-page rule",
        ),
        OcrElement(
            element_id="3" * 64,
            page_number=2,
            reading_order=0,
            element_type="formula",
            text_content=r"\[x^2\]",
            markdown_content=r"\[x^2\]",
        ),
    )

    bundle = build_page_markdown_bundle(elements, page_count=2)

    assert tuple((page.page_number, page.markdown) for page in bundle.pages) == (
        (1, "# First\n\nContains\n\n---\n\nan in-page rule"),
        (2, r"\[x^2\]"),
    )


def test_page_markdown_uses_page_relative_sdk_image_paths() -> None:
    elements = (
        OcrElement(
            element_id="1" * 64,
            page_number=1,
            reading_order=0,
            element_type="image",
            text_content="",
            markdown_content="![Image 0-0](../imgs/cropped_page0_idx0.jpg)",
        ),
        OcrElement(
            element_id="2" * 64,
            page_number=2,
            reading_order=0,
            element_type="image",
            text_content="",
            markdown_content="![Image 1-1](../imgs/cropped_page1_idx1.jpg)",
        ),
    )

    bundle = build_page_markdown_bundle(elements, page_count=2)

    assert tuple(page.markdown for page in bundle.pages) == (
        "![Image 0-0](../imgs/cropped_page0_idx0.jpg)",
        "![Image 1-1](../imgs/cropped_page1_idx1.jpg)",
    )


@pytest.mark.parametrize(
    "unsupported_path",
    [
        "document.md",
        "layout.json.gz",
        "raw_model.json.gz",
        "pages/page-0001.md",
    ],
)
def test_successful_result_rejects_stale_or_redundant_artifacts(
    unsupported_path: str,
) -> None:
    files = (
        _artifact("elements.jsonl.gz", "1"),
        _artifact("pages.json.gz", "2"),
        _artifact("layout_vis/page-0001.jpg", "3"),
        _artifact(unsupported_path, "4"),
    )

    with pytest.raises(ValueError, match="Unsupported OCR document artifact path"):
        OcrDocumentResult(
            request_id="4" * 64,
            arxiv_id="2607.00001",
            state="succeeded",
            pdf_sha256="5" * 64,
            pdf_size_bytes=100,
            page_count=1,
            processing_id="6" * 64,
            manifest_sha256="7" * 64,
            files=files,
        )


def test_page_markdown_bundle_is_bounded_and_requires_consecutive_pages() -> None:
    bundle = OcrPageMarkdownBundle(
        schema_version="1.0.0",
        pages=(
            OcrPageMarkdown(page_number=1, markdown="# One"),
            OcrPageMarkdown(page_number=2, markdown="# Two"),
        ),
    )
    compressed = gzip.compress(bundle.model_dump_json().encode(), mtime=0)

    decoded = read_page_markdown_bundle(
        BytesIO(compressed),
        max_uncompressed_bytes=1024,
    )

    assert decoded.markdown(2) == "# Two"
    with pytest.raises(InvalidPageMarkdownBundleError, match="size limit"):
        read_page_markdown_bundle(
            BytesIO(compressed),
            max_uncompressed_bytes=10,
        )
    with pytest.raises(ValueError, match="consecutive"):
        OcrPageMarkdownBundle(
            schema_version="1.0.0",
            pages=(OcrPageMarkdown(page_number=2, markdown="# Two"),),
        )
