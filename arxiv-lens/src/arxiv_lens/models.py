"""Validated read models owned by ArXiv Lens."""

from datetime import date, datetime
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LensModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OcrDocumentFilter(LensModel):
    search: str = Field(default="", max_length=200)
    limit: int = Field(default=50, ge=1, le=200)

    @field_validator("search")
    @classmethod
    def normalize_search(cls, value: str) -> str:
        return value.strip().lower()


class OcrDocumentSummary(LensModel):
    arxiv_id: str
    title: str | None = None
    page_count: int = Field(ge=1)
    processing_id: str
    model_repository: str
    model_revision: str
    processed_at: datetime


class OcrDocument(LensModel):
    arxiv_id: str
    title: str | None = None
    abstract: str | None = None
    oai_datestamp: date
    source_record_sha256: str
    pdf_url: str
    pdf_sha256: str
    pdf_size_bytes: int = Field(ge=1)
    page_count: int = Field(ge=1)
    processing_id: str
    model_repository: str
    model_revision: str
    layout_model_repository: str
    layout_model_revision: str
    sdk_version: str
    config_hash: str
    artifact_uri: str
    manifest_sha256: str
    processed_at: datetime

    @property
    def paper_url(self) -> str:
        return f"https://arxiv.org/abs/{quote(self.arxiv_id, safe='/')}"
