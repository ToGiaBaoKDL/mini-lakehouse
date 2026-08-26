"""Bounded PDF acquisition and structural inspection."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pymupdf
import requests

from document_ocr.identity import processing_id
from document_ocr.protocol import (
    OcrError,
    OcrJob,
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
    document_id: str
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
            document_id=self.document_id,
            pdf_sha256=self.pdf_sha256,
            configuration_hash=configuration_hash,
        )


def download_pdf(
    url: str,
    destination: Path,
    max_bytes: int,
) -> tuple[str, int]:
    """Download one HTTPS PDF while enforcing the configured byte limit."""
    try:
        with PDF_SESSION.get(url, stream=True, timeout=_DOWNLOAD_TIMEOUT) as response:
            if response.status_code == 404:
                raise OcrError("ArXiv returned HTTP 404", code="pdf_not_found")
            if response.status_code == 429 or response.status_code >= 500:
                raise OcrError(
                    f"ArXiv returned HTTP {response.status_code}",
                    code="pdf_source_unavailable",
                )
            response.raise_for_status()
            if not response.url.startswith("https://"):
                raise OcrError(
                    "ArXiv redirected the PDF to a non-HTTPS URL",
                    code="unsafe_pdf_redirect",
                )
            declared_size = int(response.headers.get("content-length", "0"))
            if declared_size > max_bytes:
                raise OcrError(
                    f"PDF declares {declared_size} bytes; maximum is {max_bytes}",
                    code="pdf_too_large",
                )
            digest = hashlib.sha256()
            size = 0
            with destination.open("xb") as output:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        raise OcrError(
                            f"PDF exceeds the {max_bytes}-byte limit",
                            code="pdf_too_large",
                        )
                    digest.update(chunk)
                    output.write(chunk)
    except OcrError:
        raise
    except (OSError, requests.RequestException, ValueError) as error:
        raise OcrError(str(error), code="pdf_download_failed") from error

    try:
        with destination.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise OcrError("Downloaded content is not a PDF", code="invalid_pdf")
    except OSError as error:
        raise OcrError(str(error), code="invalid_pdf") from error
    return digest.hexdigest(), size


def pdf_page_sizes(path: Path, maximum: int) -> tuple[tuple[float, float], ...]:
    """Return PDF page sizes after enforcing non-empty and page-count limits."""
    try:
        with pymupdf.open(path) as document:
            sizes = tuple((float(page.rect.width), float(page.rect.height)) for page in document)
    except Exception as error:
        raise OcrError(str(error), code="invalid_pdf") from error
    if not sizes:
        raise OcrError("PDF has no pages", code="empty_pdf")
    if len(sizes) > maximum:
        raise OcrError(
            f"PDF has {len(sizes)} pages; maximum is {maximum}",
            code="pdf_too_many_pages",
        )
    return sizes


def prepare_document(
    job: OcrJob,
    work_root: Path,
) -> PreparedDocument:
    """Download and structurally inspect one document."""
    pdf_path = work_root / "source.pdf"
    pdf_sha256, pdf_size_bytes = download_pdf(
        job.pdf_url,
        pdf_path,
        job.limits.max_pdf_bytes,
    )
    page_sizes = pdf_page_sizes(pdf_path, job.limits.max_pages_per_document)
    reuse = job.reuse
    if (
        reuse is not None
        and reuse.pdf_sha256 == pdf_sha256
        and not reuse.matches(
            pdf_sha256=pdf_sha256,
            pdf_size_bytes=pdf_size_bytes,
            page_count=len(page_sizes),
        )
    ):
        raise OcrError(
            "Previously imported OCR lineage disagrees with unchanged PDF content",
            code="reuse_lineage_mismatch",
        )
    return PreparedDocument(
        document_id=job.document_id,
        work_root=work_root,
        pdf_path=pdf_path,
        pdf_sha256=pdf_sha256,
        pdf_size_bytes=pdf_size_bytes,
        page_sizes=page_sizes,
    )
