"""Bounded PDF acquisition and structural inspection."""

import hashlib
from pathlib import Path

import pymupdf
import requests

from document_ocr.errors import DocumentProcessingError
from document_ocr.protocol import OcrDocumentRequest

_DOWNLOAD_TIMEOUT = (30, 180)
_CHUNK_SIZE = 1024 * 1024


def _pdf_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = "mini-lakehouse-document-extraction/1.0"
    return session


PDF_SESSION = _pdf_session()


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
