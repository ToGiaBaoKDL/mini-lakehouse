"""Application models owned by the curated ArXiv product."""

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mini_lakehouse.processing.ocr.core.identity import batch_id
from mini_lakehouse.processing.ocr.core.protocol import (
    OcrDocumentRequest,
    OcrJob,
    OcrReuseReference,
)
from mini_lakehouse.processing.ocr.provider import OcrProviderName


class ArxivProductModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArxivCurationResult(ArxivProductModel):
    datestamp_date: date
    source_rows: int = Field(ge=0)
    paper_rows: int = Field(ge=0)
    author_rows: int = Field(ge=0)
    category_rows: int = Field(ge=0)
    was_written: bool


class OcrRunState(StrEnum):
    PREPARED = "prepared"
    SUBMITTED = "submitted"
    RUNNING = "running"
    IMPORTED = "imported"
    RETRYABLE_FAILED = "retryable_failed"
    TERMINAL_FAILED = "terminal_failed"


class OcrCandidate(ArxivProductModel):
    arxiv_id: str
    oai_datestamp: date
    source_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pdf_url: str
    attempt_count: int = Field(ge=0)
    reuse: OcrReuseReference | None = None


class OcrBatchDocument(ArxivProductModel):
    request: OcrDocumentRequest
    attempt_count: int = Field(ge=1)
    state: OcrRunState = OcrRunState.PREPARED


class ActiveOcrBatch(ArxivProductModel):
    batch_id: str
    state: Literal["prepared", "submitted", "running"]
    provider: OcrProviderName
    provider_reference: str
    provider_run_id: str | None
    job: OcrJob
    documents: tuple[OcrBatchDocument, ...] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_durable_job(self) -> "ActiveOcrBatch":
        if self.job.batch_id != self.batch_id:
            raise ValueError("Active OCR batch and immutable job identities differ")
        requests = tuple(document.request for document in self.documents)
        if self.job.documents != requests:
            raise ValueError("Active OCR batch documents differ from its immutable job")
        attempts = {
            document.request.request_id: document.attempt_count for document in self.documents
        }
        expected_batch_id = batch_id(
            [document.request.request_id for document in self.documents],
            attempts=attempts,
        )
        if expected_batch_id != self.batch_id:
            raise ValueError("Active OCR batch identity differs from its durable attempts")
        if self.state != "prepared" and self.provider_run_id is None:
            raise ValueError("A submitted or running OCR batch requires a provider run ID")
        return self


class OcrCycleResult(ArxivProductModel):
    action: Literal[
        "idle",
        "waiting",
        "deferred_quota",
        "submitted",
        "reconciled",
        "reconciled_and_submitted",
    ]
    batch_id: str | None = None
    reconciled_batch_id: str | None = None
    imported_documents: int = Field(default=0, ge=0)
    retryable_failures: int = Field(default=0, ge=0)
    terminal_failures: int = Field(default=0, ge=0)
    remaining_gpu_quota_minutes: int | None = Field(default=None, ge=0)
    quota_refresh_at: datetime | None = None
