"""Atomic publication contract for one ArXiv OAI datestamp day."""

import hashlib
from collections.abc import Iterable
from datetime import date
from typing import Literal

from pydantic import Field, field_validator, model_validator

from lakehouse.contracts.base import ContractModel, validate_relative_prefix
from lakehouse.contracts.captures.base import Sha256


def arxiv_snapshot(page_sha256s: Iterable[str]) -> str:
    return hashlib.sha256("".join(page_sha256s).encode()).hexdigest()


class ArxivOaiPage(ContractModel):
    page: int = Field(ge=1)
    key: str
    size_bytes: int = Field(gt=0)
    sha256: Sha256

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return validate_relative_prefix(value)


class ArxivOaiManifest(ContractModel):
    schema_version: Literal[1] = 1
    source: Literal["arxiv_oai"] = "arxiv_oai"
    source_date: date
    snapshot: Sha256
    pages: tuple[ArxivOaiPage, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_ordered_pages(self) -> "ArxivOaiManifest":
        if [item.page for item in self.pages] != list(range(1, len(self.pages) + 1)):
            raise ValueError("ArXiv OAI manifest pages must be ordered from one")
        snapshot = arxiv_snapshot(item.sha256 for item in self.pages)
        if self.snapshot != snapshot:
            raise ValueError("ArXiv OAI snapshot does not match its page checksums")
        return self
