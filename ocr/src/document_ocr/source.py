"""Bounded PDF acquisition and structural inspection."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pymupdf
import requests

from document_ocr.identity import processing_id
from document_ocr.protocol import (
    DocumentJob,
    DocumentProcessingError,
    OcrDocumentRequest,
    OcrDocumentResult,
)

_DOWNLOAD_TIMEOUT = (30, 180)
_CHUNK_SIZE = 1024 * 1024


def _pdf_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = "mini-lakehouse-document-extraction/1.0"
    return session


PDF_SESSION = _pdf_session()


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    request: OcrDocumentRequest
    work_root: Path
    pdf_path: Path
    pdf_sha256: str
    pdf_size_bytes: int
    page_sizes: tuple[tuple[float, float], ...]

    @property
    def page_count(self) -> int:
        return len(self.page_sizes)

    def processing_id(self, configuration_hash: str) -> str:
        return processing_id(
            document_id=self.request.document_id,
            pdf_sha256=self.pdf_sha256,
            configuration_hash=configuration_hash,
        )


def download_pdf(
    request: OcrDocumentRequest,
    destination: Path,
    max_bytes: int,
) -> tuple[str, int]:
    """Download one HTTPS PDF while enforcing the configured byte limit."""
    try:
        with PDF_SESSION.get(request.pdf_url, stream=True, timeout=_DOWNLOAD_TIMEOUT) as response:
            if response.status_code == 404:
                raise DocumentProcessingError("pdf_not_found", "ArXiv returned HTTP 404")
            if response.status_code == 429 or response.status_code >= 500:
                raise DocumentProcessingError(
                    "pdf_source_unavailable",
                    f"ArXiv returned HTTP {response.status_code}",
                )
            response.raise_for_status()
            if not response.url.startswith("https://"):
                raise DocumentProcessingError(
                    "unsafe_pdf_redirect",
                    "ArXiv redirected the PDF to a non-HTTPS URL",
                )
            declared_size = int(response.headers.get("content-length", "0"))
            if declared_size > max_bytes:
                raise DocumentProcessingError(
                    "pdf_too_large",
                    f"PDF declares {declared_size} bytes; maximum is {max_bytes}",
                )
            digest = hashlib.sha256()
            size = 0
            with destination.open("xb") as output:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        raise DocumentProcessingError(
                            "pdf_too_large",
                            f"PDF exceeds the {max_bytes}-byte limit",
                        )
                    digest.update(chunk)
                    output.write(chunk)
    except DocumentProcessingError:
        raise
    except (OSError, requests.RequestException, ValueError) as error:
        raise DocumentProcessingError("pdf_download_failed", str(error)) from error

    try:
        with destination.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise DocumentProcessingError("invalid_pdf", "Downloaded content is not a PDF")
    except OSError as error:
        raise DocumentProcessingError("invalid_pdf", str(error)) from error
    return digest.hexdigest(), size


def pdf_page_sizes(path: Path, maximum: int) -> tuple[tuple[float, float], ...]:
    """Return PDF page sizes after enforcing non-empty and page-count limits."""
    try:
        with pymupdf.open(path) as document:
            sizes = tuple((float(page.rect.width), float(page.rect.height)) for page in document)
    except Exception as error:
        raise DocumentProcessingError("invalid_pdf", str(error)) from error
    if not sizes:
        raise DocumentProcessingError("empty_pdf", "PDF has no pages")
    if len(sizes) > maximum:
        raise DocumentProcessingError(
            "pdf_too_many_pages",
            f"PDF has {len(sizes)} pages; maximum is {maximum}",
        )
    return sizes


def prepare_document(
    job: DocumentJob,
    work_root: Path,
) -> PreparedDocument | OcrDocumentResult:
    """Download, inspect, and reuse one document through a shared lineage check."""
    request = job.document
    pdf_path = work_root / "source.pdf"
    pdf_sha256, pdf_size_bytes = download_pdf(
        request,
        pdf_path,
        job.limits.max_pdf_bytes,
    )
    page_sizes = pdf_page_sizes(pdf_path, job.limits.max_pages_per_document)
    reuse = request.reuse
    if reuse is not None and reuse.pdf_sha256 == pdf_sha256:
        if not reuse.matches(
            pdf_sha256=pdf_sha256,
            pdf_size_bytes=pdf_size_bytes,
            page_count=len(page_sizes),
        ):
            raise DocumentProcessingError(
                "reuse_lineage_mismatch",
                "Previously imported OCR lineage disagrees with unchanged PDF content",
            )
        return OcrDocumentResult(
            request_id=request.request_id,
            document_id=request.document_id,
            state="reused",
            pdf_sha256=pdf_sha256,
            pdf_size_bytes=pdf_size_bytes,
            page_count=len(page_sizes),
            processing_id=reuse.processing_id,
            manifest_sha256=reuse.manifest_sha256,
        )
    return PreparedDocument(
        request=request,
        work_root=work_root,
        pdf_path=pdf_path,
        pdf_sha256=pdf_sha256,
        pdf_size_bytes=pdf_size_bytes,
        page_sizes=page_sizes,
    )
