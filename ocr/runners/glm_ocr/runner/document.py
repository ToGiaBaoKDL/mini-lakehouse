"""Download one PDF and adapt the official GLM-OCR result to the output protocol."""

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from document_ocr.artifacts import describe_artifacts, write_elements, write_gzip_json
from document_ocr.errors import DocumentProcessingError
from document_ocr.identity import (
    canonical_json_bytes,
    canonical_json_sha256,
    processing_id,
)
from document_ocr.protocol import (
    PAGE_MARKDOWN_BUNDLE_PATH,
    OcrDocumentManifest,
    OcrDocumentRequest,
    OcrDocumentResult,
    OcrElement,
    OcrJob,
)
from document_ocr.source import download_pdf, pdf_page_sizes
from document_ocr.text import build_page_markdown_bundle
from glmocr import GlmOcr

DocumentError = DocumentProcessingError


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    request: OcrDocumentRequest
    work_root: Path
    pdf_path: Path
    pdf_sha256: str
    pdf_size_bytes: int
    page_count: int


def pdf_page_count(path: Path, maximum: int) -> int:
    return len(pdf_page_sizes(path, maximum))


def prepare_document(
    job: OcrJob,
    work_root: Path,
) -> PreparedDocument | OcrDocumentResult:
    request = job.document
    pdf_path = work_root / "source.pdf"
    pdf_sha256, pdf_size_bytes = download_pdf(
        request,
        pdf_path,
        job.limits.max_pdf_bytes,
    )
    page_count = pdf_page_count(pdf_path, job.limits.max_pages_per_document)
    reuse = request.reuse
    if reuse is not None and reuse.pdf_sha256 == pdf_sha256:
        if not reuse.matches(
            pdf_sha256=pdf_sha256,
            pdf_size_bytes=pdf_size_bytes,
            page_count=page_count,
        ):
            raise DocumentError(
                "reuse_lineage_mismatch",
                "Previously imported OCR lineage disagrees with unchanged PDF content",
            )
        return OcrDocumentResult(
            request_id=request.request_id,
            document_id=request.document_id,
            state="reused",
            pdf_sha256=pdf_sha256,
            pdf_size_bytes=pdf_size_bytes,
            page_count=page_count,
            processing_id=reuse.processing_id,
            manifest_sha256=reuse.manifest_sha256,
        )
    return PreparedDocument(
        request=request,
        work_root=work_root,
        pdf_path=pdf_path,
        pdf_sha256=pdf_sha256,
        pdf_size_bytes=pdf_size_bytes,
        page_count=page_count,
    )


def _normalized_layout(value: Any, expected_pages: int) -> list[list[dict[str, Any]]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise DocumentError("invalid_model_output", str(error)) from error
    if not isinstance(value, list):
        raise DocumentError(
            "invalid_model_output",
            "GLM-OCR JSON output must be a list of pages",
        )
    if len(value) != expected_pages:
        raise DocumentError(
            "incomplete_model_output",
            f"GLM-OCR returned {len(value)} pages for a {expected_pages}-page PDF",
        )
    pages: list[list[dict[str, Any]]] = []
    for page_index, raw_page in enumerate(value, start=1):
        if not isinstance(raw_page, list):
            raise DocumentError(
                "invalid_model_output",
                f"GLM-OCR page {page_index} is not a list",
            )
        if not all(isinstance(block, dict) for block in raw_page):
            raise DocumentError(
                "invalid_model_output",
                f"GLM-OCR page {page_index} contains a non-object block",
            )
        pages.append(raw_page)
    return pages


def _sdk_image_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str):
        raise ValueError("GLM-OCR image_path must be text")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or len(path.parts) != 2
        or path.parts[0] != "imgs"
        or path.name in {"", ".", ".."}
    ):
        raise ValueError(f"Unsafe GLM-OCR image path: {value!r}")
    return path


def _canonical_elements(
    pages: list[list[dict[str, Any]]],
    current_processing_id: str,
) -> tuple[OcrElement, ...]:
    elements: list[OcrElement] = []
    image_counter = 0
    for page_index, page in enumerate(pages, start=1):
        for reading_order, block in enumerate(page):
            content = block.get("content", "")
            label = block.get("label")
            if not isinstance(label, str) or not label:
                raise DocumentError(
                    "invalid_model_output",
                    f"Invalid GLM-OCR block {page_index}:{reading_order}: "
                    "label must be non-empty text",
                )
            # glmocr 0.1.5 deliberately emits null content for skipped image
            # regions. The SDK still treats those regions as valid and attaches
            # their cropped asset through image_path.
            if content is None and label == "image":
                content = ""
            if not isinstance(content, str):
                raise DocumentError(
                    "invalid_model_output",
                    f"Invalid GLM-OCR block {page_index}:{reading_order}: content must be text",
                )
            markdown_content = content or None
            if label == "image":
                markdown_content = None
                if block.get("image_path") is not None:
                    try:
                        image_path = _sdk_image_path(block["image_path"])
                    except ValueError as error:
                        raise DocumentError(
                            "invalid_model_output",
                            str(error),
                        ) from error
                    markdown_content = (
                        f"![Image {page_index - 1}-{image_counter}](../{image_path.as_posix()})"
                    )
                    image_counter += 1
            attributes = {
                key: block[key]
                for key in sorted(block)
                if key not in {"content", "bbox_2d", "index", "label"}
            }
            elements.append(
                OcrElement(
                    element_id=canonical_json_sha256(
                        {
                            "page_number": page_index,
                            "processing_id": current_processing_id,
                            "reading_order": reading_order,
                        }
                    ),
                    page_number=page_index,
                    reading_order=reading_order,
                    element_type=label,
                    bbox_json=(
                        json.dumps(
                            block["bbox_2d"],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        if block.get("bbox_2d") is not None
                        else None
                    ),
                    text_content=content,
                    markdown_content=markdown_content,
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
    if not elements:
        raise DocumentError(
            "empty_model_output",
            "GLM-OCR produced no canonical elements",
        )
    return tuple(elements)


def _save_sdk_images(parsed: Any, destination: Path, expected_pages: int) -> None:
    visualizations = parsed.layout_vis_images or {}
    expected = set(range(expected_pages))
    if set(visualizations) != expected:
        raise DocumentError(
            "incomplete_model_output",
            f"GLM-OCR produced visualizations for pages {sorted(visualizations)}, "
            f"expected {sorted(expected)}",
        )
    layout_target = destination / "layout_vis"
    layout_target.mkdir()
    for page_index, image in sorted(visualizations.items()):
        image.save(layout_target / f"page-{page_index + 1:04d}.jpg", quality=95)

    images = parsed.image_files or {}
    if not images:
        return
    image_target = destination / "imgs"
    image_target.mkdir()
    for name, image in sorted(images.items()):
        path = PurePosixPath(name)
        if path.name != name or name in {"", ".", ".."}:
            raise DocumentError(
                "invalid_model_output",
                f"Unsafe GLM-OCR image filename: {name!r}",
            )
        image.save(image_target / name)


def process_document(
    job: OcrJob,
    prepared: PreparedDocument,
    parser: GlmOcr,
    output_root: Path,
) -> tuple[OcrDocumentResult, OcrDocumentManifest]:
    request = prepared.request
    current_processing_id = processing_id(
        document_id=request.document_id,
        pdf_sha256=prepared.pdf_sha256,
        configuration_hash=job.config_hash,
    )
    try:
        parsed = parser.parse(prepared.pdf_path, save_layout_visualization=True)
    except Exception as error:
        raise DocumentError("ocr_inference_failed", str(error)) from error

    document_root = prepared.work_root / "committed-output"
    document_root.mkdir()
    try:
        elements = _canonical_elements(
            _normalized_layout(parsed.json_result, prepared.page_count),
            current_processing_id,
        )
        write_gzip_json(
            document_root.joinpath(*PAGE_MARKDOWN_BUNDLE_PATH.parts),
            build_page_markdown_bundle(elements, page_count=prepared.page_count),
        )
        write_elements(document_root / "elements.jsonl.gz", elements)
        _save_sdk_images(parsed, document_root, prepared.page_count)
        files = describe_artifacts(document_root, job.limits.max_output_bytes)
    except DocumentError:
        raise
    except (OSError, ValueError) as error:
        raise DocumentError("artifact_generation_failed", str(error)) from error

    manifest = OcrDocumentManifest(
        document_id=request.document_id,
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
        raise DocumentError(
            "ocr_output_too_large",
            f"OCR output exceeds the {job.limits.max_output_bytes}-byte limit",
        )
    result = OcrDocumentResult(
        request_id=request.request_id,
        document_id=request.document_id,
        state="succeeded",
        pdf_sha256=prepared.pdf_sha256,
        pdf_size_bytes=prepared.pdf_size_bytes,
        page_count=prepared.page_count,
        processing_id=current_processing_id,
        manifest_sha256=canonical_json_sha256(manifest.model_dump(mode="json")),
    )
    destination = output_root
    destination.parent.mkdir(parents=True, exist_ok=True)
    document_root.replace(destination)
    return result, manifest
