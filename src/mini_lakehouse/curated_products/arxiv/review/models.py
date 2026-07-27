"""Typed read models for the ArXiv OCR review surface."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from mini_lakehouse.curated_products.arxiv.models import OcrRunState
from mini_lakehouse.processing.ocr.core.protocol import (
    ArtifactFile,
    validate_document_artifact_paths,
)


class OcrStateFilter(StrEnum):
    ALL = "all"
    IMPORTED = OcrRunState.IMPORTED.value
    RUNNING = OcrRunState.RUNNING.value
    SUBMITTED = OcrRunState.SUBMITTED.value
    PREPARED = OcrRunState.PREPARED.value
    RETRYABLE_FAILED = OcrRunState.RETRYABLE_FAILED.value
    TERMINAL_FAILED = OcrRunState.TERMINAL_FAILED.value


class OcrReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OcrDocumentFilter(OcrReviewModel):
    search: str = Field(default="", max_length=200)
    state: OcrStateFilter = OcrStateFilter.ALL
    limit: int = Field(default=50, ge=1, le=200)


class OcrDocumentSummary(OcrReviewModel):
    arxiv_id: str
    title: str | None = None
    state: OcrRunState
    attempt_count: int = Field(ge=1)
    page_count: int | None = Field(default=None, ge=1)
    processing_id: str | None = None
    model_repository: str
    model_revision: str
    completed_at: datetime | None = None
    error_code: str | None = None


class OcrDocumentRun(OcrReviewModel):
    request_id: str
    batch_id: str
    arxiv_id: str
    title: str | None = None
    abstract: str | None = None
    pdf_url: str
    oai_datestamp: date
    state: OcrRunState
    attempt_count: int = Field(ge=1)
    processing_id: str | None = None
    artifact_uri: str | None = None
    manifest_sha256: str | None = None
    pdf_sha256: str | None = None
    pdf_size_bytes: int | None = Field(default=None, ge=0)
    page_count: int | None = Field(default=None, ge=1)
    model_repository: str
    model_revision: str
    layout_model_repository: str
    layout_model_revision: str
    adapter_version: str
    error_code: str | None = None
    error_message: str | None = None
    prepared_at: datetime
    completed_at: datetime | None = None

    @computed_field
    @property
    def run_key(self) -> str:
        return f"{self.batch_id}:{self.request_id}"

    @computed_field
    @property
    def artifacts_available(self) -> bool:
        return (
            self.state == OcrRunState.IMPORTED
            and self.processing_id is not None
            and self.artifact_uri is not None
            and self.manifest_sha256 is not None
            and self.page_count is not None
        )

    @property
    def paper_url(self) -> str:
        return f"https://arxiv.org/abs/{quote(self.arxiv_id, safe='/')}"


class OcrPageElement(OcrReviewModel):
    element_id: str
    page_number: int = Field(ge=1)
    reading_order: int = Field(ge=0)
    element_type: str
    bbox_json: str | None = None
    text_content: str
    markdown_content: str | None = None
    parent_element_id: str | None = None
    raw_attributes_json: str | None = None


class PublishedOcrManifest(OcrReviewModel):
    arxiv_id: str
    files: tuple[ArtifactFile, ...]
    page_count: int = Field(ge=1)
    pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pdf_size_bytes: int = Field(ge=0)
    processing_id: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_artifact_set(self) -> PublishedOcrManifest:
        paths = [file.relative_path for file in self.files]
        validate_document_artifact_paths(paths, page_count=self.page_count)
        return self

    def file(self, relative_path: str) -> ArtifactFile:
        try:
            return next(file for file in self.files if file.relative_path == relative_path)
        except StopIteration as error:
            raise FileNotFoundError(f"OCR artifact is not declared: {relative_path}") from error
