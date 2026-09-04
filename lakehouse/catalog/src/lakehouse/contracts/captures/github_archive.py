"""Atomic publication contract for one GitHub Archive UTC day."""

from datetime import date, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from lakehouse.contracts.base import ContractModel, validate_relative_prefix
from lakehouse.contracts.captures.base import Sha256


class GitHubArchiveObject(ContractModel):
    hour: int = Field(ge=0, le=23)
    key: str
    size_bytes: int = Field(gt=0)
    sha256: Sha256
    last_modified: datetime

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return validate_relative_prefix(value)

    @field_validator("last_modified")
    @classmethod
    def require_timestamp_offset(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("last_modified must include a UTC offset")
        return value


class GitHubArchiveManifest(ContractModel):
    schema_version: Literal[1] = 1
    source: Literal["github_archive"] = "github_archive"
    source_date: date
    objects: tuple[GitHubArchiveObject, ...] = Field(min_length=24, max_length=24)

    @model_validator(mode="after")
    def require_complete_ordered_day(self) -> "GitHubArchiveManifest":
        if [item.hour for item in self.objects] != list(range(24)):
            raise ValueError("GitHub Archive manifest must contain ordered hours 00 through 23")
        return self
