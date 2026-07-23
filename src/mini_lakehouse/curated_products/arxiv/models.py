"""Application models owned by the curated ArXiv product."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mini_lakehouse.processing.ocr.protocol import OcrDocumentRequest


class ArxivProductModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArxivCurationResult(ArxivProductModel):
    datestamp_date: date
    source_rows: int = Field(ge=0)
    paper_rows: int = Field(ge=0)
    author_rows: int = Field(ge=0)
    category_rows: int = Field(ge=0)
    was_written: bool


class OcrCandidate(ArxivProductModel):
    arxiv_id: str
    oai_datestamp: date
    source_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pdf_url: str
    attempt_count: int = Field(ge=0)


class OcrBatchDocument(ArxivProductModel):
    request: OcrDocumentRequest
    attempt_count: int = Field(ge=1)
    state: Literal[
        "prepared",
        "submitted",
        "running",
        "imported",
        "retryable_failed",
        "terminal_failed",
    ] = "prepared"


class ActiveOcrBatch(ArxivProductModel):
    batch_id: str
    state: Literal["prepared", "submitted", "running"]
    provider_run_id: str | None
    documents: tuple[OcrBatchDocument, ...] = Field(min_length=1, max_length=10)


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
