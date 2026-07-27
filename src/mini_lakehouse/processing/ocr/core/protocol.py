"""Strict, provider-neutral JSON protocol shared with remote OCR runners."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mini_lakehouse.processing.ocr.core.identity import request_id
from mini_lakehouse.processing.ocr.core.paths import PAGE_MARKDOWN_BUNDLE_PATH

Sha256 = str
OCR_OUTPUT_SCHEMA_VERSION = "1.1.0"
DocumentState = Literal[
    "succeeded",
    "retryable_failed",
    "terminal_failed",
]


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OcrDocumentRequest(ProtocolModel):
    request_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    arxiv_id: str = Field(min_length=1, max_length=255)
    oai_datestamp: date
    source_record_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    pdf_url: str = Field(pattern=r"^https://arxiv\.org/pdf/")


class OcrLimits(ProtocolModel):
    max_pdf_bytes: int = Field(ge=1024 * 1024)
    max_pages_per_document: int = Field(ge=1)
    max_output_bytes: int = Field(ge=1024 * 1024)


class OcrModel(ProtocolModel):
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")


class OcrInference(ProtocolModel):
    api_port: int = Field(ge=1024, le=65535)
    # ``half`` is retained only for immutable schema-v1 jobs already persisted
    # before the canonical spelling was changed to ``float16``. vLLM treats the
    # two values as the same dtype; new processor contracts emit ``float16``.
    dtype: Literal["half", "float16"]
    max_model_len: int = Field(ge=4096)
    gpu_memory_utilization: float = Field(gt=0, le=0.9)
    speculative_tokens: int = Field(ge=0, le=8)
    enforce_eager: bool = False
    max_num_seqs: int = Field(default=1, ge=1, le=32)
    max_workers: int = Field(ge=1, le=32)
    request_timeout_seconds: int = Field(default=600, ge=30, le=3600)
    layout_device: Literal["cpu", "cuda:0"]


class OcrJob(ProtocolModel):
    schema_version: Literal["1.0.0"]
    batch_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    runner_bundle_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    model: OcrModel
    layout_model: OcrModel
    adapter_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    output_schema_version: Literal["1.1.0"]
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    limits: OcrLimits
    inference: OcrInference
    documents: tuple[OcrDocumentRequest, ...] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def unique_documents(self) -> OcrJob:
        request_ids = [document.request_id for document in self.documents]
        arxiv_ids = [document.arxiv_id for document in self.documents]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("OCR job request IDs must be unique")
        if len(arxiv_ids) != len(set(arxiv_ids)):
            raise ValueError("An OCR job may contain a paper only once")
        for document in self.documents:
            expected_request_id = request_id(
                arxiv_id=document.arxiv_id,
                source_record_sha256=document.source_record_sha256,
                configuration_hash=self.config_hash,
            )
            if document.request_id != expected_request_id:
                raise ValueError(
                    f"OCR request identity does not match job configuration for {document.arxiv_id}"
                )
        return self


class ArtifactFile(ProtocolModel):
    relative_path: str
    sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or not value or value != path.as_posix() or ".." in path.parts:
            raise ValueError("Artifact paths must be normalized relative POSIX paths")
        return value


class OcrElement(ProtocolModel):
    element_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_number: int = Field(ge=1)
    reading_order: int = Field(ge=0)
    element_type: str = Field(min_length=1, max_length=100)
    bbox_json: str | None = None
    text_content: str
    markdown_content: str | None = None
    parent_element_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    raw_attributes_json: str | None = None


class OcrPageMarkdown(ProtocolModel):
    page_number: int = Field(ge=1)
    markdown: str


class OcrPageMarkdownBundle(ProtocolModel):
    schema_version: Literal["1.0.0"]
    pages: tuple[OcrPageMarkdown, ...] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def consecutive_pages(self) -> OcrPageMarkdownBundle:
        page_numbers = tuple(page.page_number for page in self.pages)
        expected = tuple(range(1, len(self.pages) + 1))
        if page_numbers != expected:
            raise ValueError("OCR page Markdown numbers must be consecutive and ordered")
        return self

    def markdown(self, page_number: int) -> str:
        if page_number < 1 or page_number > len(self.pages):
            raise ValueError(f"Page {page_number} is outside this document")
        return self.pages[page_number - 1].markdown


def validate_document_artifact_paths(paths: list[str], *, page_count: int) -> None:
    if len(paths) != len(set(paths)):
        raise ValueError("OCR result artifact paths must be unique")
    required = {PAGE_MARKDOWN_BUNDLE_PATH.as_posix(), "elements.jsonl.gz"}
    if not required.issubset(paths):
        raise ValueError("Successful OCR results require pages.json.gz and elements.jsonl.gz")

    expected_visualizations = {
        f"layout_vis/page-{page_number:04d}" for page_number in range(1, page_count + 1)
    }
    actual_visualizations: list[str] = []
    for value in paths:
        path = PurePosixPath(value)
        if value in required:
            continue
        if (
            len(path.parts) == 2
            and path.parts[0] == "layout_vis"
            and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        ):
            actual_visualizations.append(path.with_suffix("").as_posix())
            continue
        if (
            len(path.parts) == 2
            and path.parts[0] == "imgs"
            and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        ):
            continue
        raise ValueError(f"Unsupported OCR document artifact path: {value}")
    if (
        len(actual_visualizations) != page_count
        or set(actual_visualizations) != expected_visualizations
    ):
        raise ValueError("Successful OCR results require exactly one visualization per page")


class OcrDocumentResult(ProtocolModel):
    request_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    arxiv_id: str = Field(min_length=1, max_length=255)
    state: DocumentState
    pdf_sha256: Sha256 | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    pdf_size_bytes: int | None = Field(default=None, ge=0)
    page_count: int | None = Field(default=None, ge=1)
    processing_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: Sha256 | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error_code: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=2000)
    files: tuple[ArtifactFile, ...] = ()

    @model_validator(mode="after")
    def validate_outcome(self) -> OcrDocumentResult:
        if self.state == "succeeded":
            required = (
                self.pdf_sha256,
                self.pdf_size_bytes,
                self.page_count,
                self.processing_id,
                self.manifest_sha256,
            )
            if any(value is None for value in required):
                raise ValueError("Successful OCR results require complete content lineage")
            assert self.page_count is not None
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("Successful OCR results cannot contain an error")
            paths = [file.relative_path for file in self.files]
            validate_document_artifact_paths(paths, page_count=self.page_count)
        else:
            if self.error_code is None or self.error_message is None:
                raise ValueError("Failed OCR results require a typed error")
            if self.processing_id is not None or self.files:
                raise ValueError("Failed OCR results cannot publish artifacts or elements")
        return self


class OcrBatchManifest(ProtocolModel):
    schema_version: Literal["1.0.0"]
    batch_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    archive_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    archive_size_bytes: int = Field(ge=1)
    documents: tuple[OcrDocumentResult, ...] = Field(min_length=1, max_length=10)

    @field_validator("created_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Batch timestamps must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def unique_results(self) -> OcrBatchManifest:
        request_ids = [document.request_id for document in self.documents]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("OCR result request IDs must be unique")
        return self
