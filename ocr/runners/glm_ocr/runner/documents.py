"""Download one PDF and adapt the official GLM-OCR result to the output protocol."""

import gzip
import hashlib
import io
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import fitz
import requests
from document_ocr.identity import (
    canonical_json_sha256,
    file_sha256,
    processing_id,
    successful_document_manifest_sha256,
)
from document_ocr.paths import (
    PAGE_MARKDOWN_BUNDLE_PATH,
    runner_document_path,
)
from document_ocr.protocol import (
    ArtifactFile,
    OcrDocumentRequest,
    OcrDocumentResult,
    OcrElement,
    OcrJob,
    OcrPageMarkdownBundle,
)
from document_ocr.text import build_page_markdown_bundle
from glmocr import GlmOcr
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ERROR_MESSAGE_CHARACTERS = 2_000
MEDIA_TYPES = {
    ".gz": "application/gzip",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


class DocumentError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    request: OcrDocumentRequest
    work_root: Path
    pdf_path: Path
    pdf_sha256: str
    pdf_size_bytes: int
    page_count: int


def _pdf_session() -> requests.Session:
    retry = Retry(
        total=4,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    session.headers["User-Agent"] = "document-ocr-arxiv/1.0"
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


PDF_SESSION = _pdf_session()


def download_pdf(
    request: OcrDocumentRequest,
    destination: Path,
    max_bytes: int,
) -> tuple[str, int]:
    try:
        with PDF_SESSION.get(request.pdf_url, stream=True, timeout=(30, 180)) as response:
            if response.status_code == 404:
                raise DocumentError("pdf_not_found", "ArXiv returned HTTP 404", retryable=False)
            if response.status_code == 429 or response.status_code >= 500:
                raise DocumentError(
                    "pdf_source_unavailable",
                    f"ArXiv returned HTTP {response.status_code}",
                    retryable=True,
                )
            response.raise_for_status()
            if not response.url.startswith("https://"):
                raise DocumentError(
                    "unsafe_pdf_redirect",
                    "ArXiv redirected the PDF to a non-HTTPS URL",
                    retryable=False,
                )
            declared_size = int(response.headers.get("content-length", "0"))
            if declared_size > max_bytes:
                raise DocumentError(
                    "pdf_too_large",
                    f"PDF declares {declared_size} bytes; maximum is {max_bytes}",
                    retryable=False,
                )
            digest = hashlib.sha256()
            size = 0
            with destination.open("xb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        raise DocumentError(
                            "pdf_too_large",
                            f"PDF exceeds the {max_bytes}-byte limit",
                            retryable=False,
                        )
                    digest.update(chunk)
                    output.write(chunk)
    except DocumentError:
        raise
    except (OSError, requests.RequestException) as error:
        raise DocumentError("pdf_download_failed", str(error), retryable=True) from error
    with destination.open("rb") as source:
        if source.read(5) != b"%PDF-":
            raise DocumentError("invalid_pdf", "Downloaded content is not a PDF", retryable=False)
    return digest.hexdigest(), size


def pdf_page_count(path: Path, maximum: int) -> int:
    try:
        with fitz.open(path) as document:
            pages = document.page_count
    except Exception as error:
        raise DocumentError("invalid_pdf", str(error), retryable=False) from error
    if pages < 1:
        raise DocumentError("empty_pdf", "PDF has no pages", retryable=False)
    if pages > maximum:
        raise DocumentError(
            "pdf_too_many_pages",
            f"PDF has {pages} pages; maximum is {maximum}",
            retryable=False,
        )
    return pages


def prepare_document(
    job: OcrJob,
    request: OcrDocumentRequest,
    work_root: Path,
) -> PreparedDocument | OcrDocumentResult:
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
                retryable=False,
            )
        return OcrDocumentResult(
            request_id=request.request_id,
            arxiv_id=request.arxiv_id,
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
            raise DocumentError("invalid_model_output", str(error), retryable=True) from error
    if not isinstance(value, list):
        raise DocumentError(
            "invalid_model_output",
            "GLM-OCR JSON output must be a list of pages",
            retryable=True,
        )
    if len(value) != expected_pages:
        raise DocumentError(
            "incomplete_model_output",
            f"GLM-OCR returned {len(value)} pages for a {expected_pages}-page PDF",
            retryable=True,
        )
    pages: list[list[dict[str, Any]]] = []
    for page_index, raw_page in enumerate(value, start=1):
        if not isinstance(raw_page, list):
            raise DocumentError(
                "invalid_model_output",
                f"GLM-OCR page {page_index} is not a list",
                retryable=True,
            )
        if not all(isinstance(block, dict) for block in raw_page):
            raise DocumentError(
                "invalid_model_output",
                f"GLM-OCR page {page_index} contains a non-object block",
                retryable=True,
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
            if not isinstance(content, str) or not isinstance(label, str) or not label:
                raise DocumentError(
                    "invalid_model_output",
                    f"Invalid GLM-OCR block {page_index}:{reading_order}",
                    retryable=True,
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
                            retryable=True,
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
            retryable=True,
        )
    return tuple(elements)


def _write_gzip_json(path: Path, value: OcrPageMarkdownBundle) -> None:
    with (
        path.open("xb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        io.TextIOWrapper(compressed, encoding="utf-8") as output,
    ):
        output.write(value.model_dump_json())


def _write_elements(path: Path, elements: Iterable[OcrElement]) -> None:
    with (
        path.open("xb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        io.TextIOWrapper(compressed, encoding="utf-8") as output,
    ):
        for element in elements:
            output.write(element.model_dump_json())
            output.write("\n")


def _save_sdk_images(parsed: Any, destination: Path, expected_pages: int) -> None:
    visualizations = parsed.layout_vis_images or {}
    expected = set(range(expected_pages))
    if set(visualizations) != expected:
        raise DocumentError(
            "incomplete_model_output",
            f"GLM-OCR produced visualizations for pages {sorted(visualizations)}, "
            f"expected {sorted(expected)}",
            retryable=True,
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
                retryable=True,
            )
        image.save(image_target / name)


def _artifact_files(root: Path, maximum_bytes: int) -> tuple[ArtifactFile, ...]:
    files: list[ArtifactFile] = []
    total = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        size = path.stat().st_size
        total += size
        if total > maximum_bytes:
            raise DocumentError(
                "ocr_output_too_large",
                f"OCR output exceeds the {maximum_bytes}-byte limit",
                retryable=False,
            )
        files.append(
            ArtifactFile(
                relative_path=path.relative_to(root).as_posix(),
                sha256=file_sha256(path),
                size_bytes=size,
                media_type=MEDIA_TYPES.get(
                    path.suffix.lower(),
                    "application/octet-stream",
                ),
            )
        )
    return tuple(files)


def process_document(
    job: OcrJob,
    prepared: PreparedDocument,
    parser: GlmOcr,
    output_root: Path,
) -> OcrDocumentResult:
    request = prepared.request
    current_processing_id = processing_id(
        arxiv_id=request.arxiv_id,
        pdf_sha256=prepared.pdf_sha256,
        configuration_hash=job.config_hash,
    )
    try:
        parsed = parser.parse(prepared.pdf_path, save_layout_visualization=True)
    except Exception as error:
        raise DocumentError("ocr_inference_failed", str(error), retryable=True) from error

    document_root = prepared.work_root / "committed-output"
    document_root.mkdir()
    try:
        elements = _canonical_elements(
            _normalized_layout(parsed.json_result, prepared.page_count),
            current_processing_id,
        )
        _write_gzip_json(
            document_root.joinpath(*PAGE_MARKDOWN_BUNDLE_PATH.parts),
            build_page_markdown_bundle(elements, page_count=prepared.page_count),
        )
        _write_elements(document_root / "elements.jsonl.gz", elements)
        _save_sdk_images(parsed, document_root, prepared.page_count)
        files = _artifact_files(document_root, job.limits.max_output_bytes)
    except DocumentError:
        raise
    except (OSError, ValueError) as error:
        raise DocumentError("artifact_generation_failed", str(error), retryable=True) from error

    serialized_files = [file.model_dump(mode="json") for file in files]
    result = OcrDocumentResult(
        request_id=request.request_id,
        arxiv_id=request.arxiv_id,
        state="succeeded",
        pdf_sha256=prepared.pdf_sha256,
        pdf_size_bytes=prepared.pdf_size_bytes,
        page_count=prepared.page_count,
        processing_id=current_processing_id,
        manifest_sha256=successful_document_manifest_sha256(
            arxiv_id=request.arxiv_id,
            pdf_sha256=prepared.pdf_sha256,
            pdf_size_bytes=prepared.pdf_size_bytes,
            page_count=prepared.page_count,
            processing_id=current_processing_id,
            files=serialized_files,
        ),
        files=files,
    )
    destination = output_root.joinpath(
        *runner_document_path(request.arxiv_id, request.request_id).parts
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    document_root.replace(destination)
    return result


def failed_result(
    request: OcrDocumentRequest,
    error: DocumentError,
) -> OcrDocumentResult:
    return OcrDocumentResult(
        request_id=request.request_id,
        arxiv_id=request.arxiv_id,
        state="retryable_failed" if error.retryable else "terminal_failed",
        error_code=error.code,
        error_message=str(error)[:ERROR_MESSAGE_CHARACTERS],
    )
