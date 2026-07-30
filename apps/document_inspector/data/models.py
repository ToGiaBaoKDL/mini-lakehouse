"""Typed read models owned by Document Inspector."""

from datetime import date, datetime
from enum import StrEnum
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, computed_field


class OcrRunState(StrEnum):
    PREPARED = "prepared"
    SUBMITTED = "submitted"
    RUNNING = "running"
    IMPORTED = "imported"
    RETRYABLE_FAILED = "retryable_failed"
    TERMINAL_FAILED = "terminal_failed"


class OcrStateFilter(StrEnum):
    ALL = "all"
    IMPORTED = OcrRunState.IMPORTED.value
    RUNNING = OcrRunState.RUNNING.value
    SUBMITTED = OcrRunState.SUBMITTED.value
    PREPARED = OcrRunState.PREPARED.value
    RETRYABLE_FAILED = OcrRunState.RETRYABLE_FAILED.value
    TERMINAL_FAILED = OcrRunState.TERMINAL_FAILED.value


class DocumentInspectorModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OcrDocumentFilter(DocumentInspectorModel):
    search: str = Field(default="", max_length=200)
    state: OcrStateFilter = OcrStateFilter.ALL
    limit: int = Field(default=50, ge=1, le=200)


class OcrDocumentSummary(DocumentInspectorModel):
    arxiv_id: str
    title: str | None = None
    state: OcrRunState
    attempt: int = Field(ge=1)
    page_count: int | None = Field(default=None, ge=1)
    processing_id: str | None = None
    model_repository: str
    model_revision: str
    completed_at: datetime | None = None
    error_code: str | None = None


class OcrDocumentRun(DocumentInspectorModel):
    run_id: str
    request_id: str
    arxiv_id: str
    title: str | None = None
    abstract: str | None = None
    pdf_url: str
    oai_datestamp: date
    state: OcrRunState
    attempt: int = Field(ge=1)
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
