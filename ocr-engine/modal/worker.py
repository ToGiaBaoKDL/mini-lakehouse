"""Run one PDF through GLM-OCR and commit the normalized output protocol."""

import json
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from document_ocr.artifacts import (
    build_document_manifest,
    commit_output,
    describe_artifacts,
    reset_output,
    write_elements,
    write_gzip_json,
)
from document_ocr.identity import canonical_json_sha256
from document_ocr.protocol import (
    PAGE_MARKDOWN_BUNDLE_PATH,
    OcrDocumentManifest,
    OcrElement,
    OcrError,
    OcrJob,
)
from document_ocr.source import PreparedDocument, prepare_document
from document_ocr.text import build_page_markdown_bundle
from glmocr import GlmOcr


def _normalized_layout(value: Any, expected_pages: int) -> list[list[dict[str, Any]]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise OcrError(str(error), code="invalid_model_output") from error
    if not isinstance(value, list):
        raise OcrError(
            "GLM-OCR JSON output must be a list of pages",
            code="invalid_model_output",
        )
    if len(value) != expected_pages:
        raise OcrError(
            f"GLM-OCR returned {len(value)} pages for a {expected_pages}-page PDF",
            code="incomplete_model_output",
        )
    pages: list[list[dict[str, Any]]] = []
    for page_index, raw_page in enumerate(value, start=1):
        if not isinstance(raw_page, list):
            raise OcrError(
                f"GLM-OCR page {page_index} is not a list",
                code="invalid_model_output",
            )
        if not all(isinstance(block, dict) for block in raw_page):
            raise OcrError(
                f"GLM-OCR page {page_index} contains a non-object block",
                code="invalid_model_output",
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
                raise OcrError(
                    f"Invalid GLM-OCR block {page_index}:{reading_order}: "
                    "label must be non-empty text",
                    code="invalid_model_output",
                )
            # glmocr 0.1.5 deliberately emits null content for skipped image
            # regions. The SDK still treats those regions as valid and attaches
            # their cropped asset through image_path.
            if content is None and label == "image":
                content = ""
            if not isinstance(content, str):
                raise OcrError(
                    f"Invalid GLM-OCR block {page_index}:{reading_order}: content must be text",
                    code="invalid_model_output",
                )
            markdown_content = content or None
            if label == "image":
                markdown_content = None
                if block.get("image_path") is not None:
                    try:
                        image_path = _sdk_image_path(block["image_path"])
                    except ValueError as error:
                        raise OcrError(str(error), code="invalid_model_output") from error
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
        raise OcrError(
            "GLM-OCR produced no canonical elements",
            code="empty_model_output",
        )
    return tuple(elements)


def _save_sdk_artifacts(parsed: Any, destination: Path, expected_pages: int) -> None:
    sdk_output = destination.parent / "sdk-output"
    parsed.save(sdk_output, save_layout_visualization=True)
    roots = [path for path in sdk_output.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise OcrError(
            f"GLM-OCR SDK produced {len(roots)} output roots; expected one",
            code="incomplete_model_output",
        )
    sdk_root = roots[0]
    visualizations = sorted((sdk_root / "layout_vis").glob("*.jpg"))
    if len(visualizations) != expected_pages:
        raise OcrError(
            f"GLM-OCR SDK produced {len(visualizations)} visualizations; expected {expected_pages}",
            code="incomplete_model_output",
        )
    layout_target = destination / "layout_vis"
    layout_target.mkdir()
    for page_number, source in enumerate(visualizations, start=1):
        shutil.move(source, layout_target / f"page-{page_number:04d}.jpg")

    images = sdk_root / "imgs"
    if not images.is_dir():
        return
    for path in images.iterdir():
        if not path.is_file() or path.name in {"", ".", ".."}:
            raise OcrError(
                f"Unsafe GLM-OCR SDK image output: {path.name!r}",
                code="invalid_model_output",
            )
    shutil.move(images, destination / "imgs")


def process_document(
    job: OcrJob,
    prepared: PreparedDocument,
    parser: GlmOcr,
    output_root: Path,
) -> OcrDocumentManifest:
    current_processing_id = prepared.processing_id(job.config_hash)
    try:
        parsed = parser.parse(prepared.pdf_path, save_layout_visualization=True)
    except Exception as error:
        raise OcrError(str(error), code="ocr_inference_failed") from error

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
        _save_sdk_artifacts(parsed, document_root, prepared.page_count)
        files = describe_artifacts(document_root, job.limits.max_output_bytes)
    except OcrError:
        raise
    except (OSError, ValueError) as error:
        raise OcrError(str(error), code="artifact_generation_failed") from error

    manifest = build_document_manifest(job, prepared, files)
    destination = output_root
    destination.parent.mkdir(parents=True, exist_ok=True)
    document_root.replace(destination)
    return manifest


def run(job: OcrJob, output_directory: Path, *, parser: GlmOcr) -> None:
    """Run one PDF and publish its result marker only after all artifacts exist."""
    print(f"OCR started for {job.document_id}", flush=True)
    reset_output(output_directory)

    with tempfile.TemporaryDirectory(prefix="glm-ocr-") as temporary_directory:
        temporary = Path(temporary_directory)
        artifacts = temporary / "artifacts"
        work = temporary / "work"
        work.mkdir()
        prepared = prepare_document(job, work)
        if job.reuse is not None and job.reuse.matches(
            pdf_sha256=prepared.pdf_sha256,
            pdf_size_bytes=prepared.pdf_size_bytes,
            page_count=prepared.page_count,
        ):
            manifest = None
            print("PDF is unchanged; reusing published artifacts", flush=True)
        else:
            manifest = process_document(job, prepared, parser, artifacts)
            print(f"GLM-OCR processed {prepared.page_count} pages", flush=True)

        commit_output(job, prepared, manifest, artifacts, output_directory)
        print(f"OCR output committed for {job.document_id}", flush=True)
