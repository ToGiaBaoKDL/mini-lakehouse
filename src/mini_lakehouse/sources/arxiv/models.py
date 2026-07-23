from datetime import UTC, date, datetime, timedelta
from typing import Self

from pydantic import BaseModel, ConfigDict, Field


class OaiDay(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: date

    @classmethod
    def previous_closed_day(cls, now: datetime | None = None) -> Self:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        return cls(value=current.date() - timedelta(days=1))

    @classmethod
    def parse(cls, value: date | str | None) -> Self:
        if value is None:
            return cls.previous_closed_day()
        if isinstance(value, date):
            return cls(value=value)
        return cls(value=date.fromisoformat(value))

    @property
    def iso(self) -> str:
        return self.value.isoformat()


class ArxivMetadataResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    datestamp_date: date
    raw_uri: str
    page_count: int = Field(ge=1)
    record_count: int = Field(ge=0)
    records_snapshot_id: int | None = None
    checkpoint_snapshot_id: int | None = None
    was_written: bool
