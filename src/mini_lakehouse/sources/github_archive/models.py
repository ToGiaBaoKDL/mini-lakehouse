"""Source boundary and ingestion result models."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ArchiveHour(BaseModel):
    value: datetime

    @field_validator("value")
    @classmethod
    def require_utc_hour(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Archive hour must include a timezone")
        utc_value = value.astimezone(UTC)
        if utc_value.minute or utc_value.second or utc_value.microsecond:
            raise ValueError("Archive hour must be aligned to the start of an hour")
        return utc_value

    @classmethod
    def previous_complete_hour(cls, now: datetime | None = None) -> Self:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        return cls(value=current.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1))

    @classmethod
    def parse(cls, value: str | datetime | None) -> Self:
        if value is None:
            return cls.previous_complete_hour()
        if isinstance(value, datetime):
            return cls(value=value)
        normalized = value.replace("Z", "+00:00")
        return cls(value=datetime.fromisoformat(normalized))

    @classmethod
    def resolve_window(
        cls,
        start: str | datetime | None,
        end: str | datetime | None,
        *,
        now: datetime | None = None,
    ) -> tuple[Self, Self]:
        if start is None:
            if end is not None:
                raise ValueError("end requires start")
            current = cls.previous_complete_hour(now)
            return current, current
        window_start = cls.parse(start)
        window_end = cls.parse(end) if end is not None else window_start
        if window_start.value > window_end.value:
            raise ValueError("start must be less than or equal to end")
        return window_start, window_end

    @property
    def filename(self) -> str:
        return self.value.strftime("%Y-%m-%d-%-H.json.gz")

    @property
    def partition_path(self) -> str:
        return self.value.strftime("year=%Y/month=%m/day=%d/hour=%H")


class GithubActor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    login: str | None = None


class GithubRepository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    name: str | None = None


class GithubArchiveEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    actor: GithubActor = Field(default_factory=GithubActor)
    repo: GithubRepository = Field(default_factory=GithubRepository)
    payload: dict[str, Any] = Field(default_factory=dict)
    public: bool = True
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value.astimezone(UTC)

    def to_record(
        self,
        *,
        source_file: str,
        source_hour: datetime,
        ingested_at: datetime,
        raw_event_json: str,
    ) -> dict[str, Any]:
        return {
            "event_id": self.id,
            "event_type": self.type,
            "actor_id": self.actor.id,
            "actor_login": self.actor.login,
            "repository_id": self.repo.id,
            "repository_name": self.repo.name,
            "payload_json": json.dumps(self.payload, separators=(",", ":"), ensure_ascii=False),
            "is_public": self.public,
            "occurred_at": self.created_at,
            "source_file": source_file,
            "source_hour": source_hour,
            "ingested_at": ingested_at,
            "raw_event_json": raw_event_json,
        }


class IngestionResult(BaseModel):
    archive_hour: datetime
    source_file: str
    raw_uri: str
    row_count: int = Field(ge=0)
    rejected_row_count: int = Field(ge=0)
    snapshot_id: int | None = None
    was_written: bool

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.row_count == 0:
            raise ValueError("An ingestion result cannot contain zero accepted rows")
        return self
