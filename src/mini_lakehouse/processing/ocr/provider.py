"""Provider-neutral remote execution contract for OCR batches."""

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from mini_lakehouse.processing.ocr.core.protocol import OcrJob

type OcrProviderName = Literal["kaggle", "modal"]


class OcrProviderState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"

    @property
    def active(self) -> bool:
        return self in {self.QUEUED, self.RUNNING}


class OcrProviderError(RuntimeError):
    pass


class OcrRunNotFoundError(OcrProviderError):
    pass


class OcrRunStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_run_id: str
    state: OcrProviderState
    failure_message: str | None = None


class OcrProviderCapacity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ready: bool
    remaining_minutes: int | None = Field(default=None, ge=0)
    refresh_at: datetime | None = None


class OcrProvider(Protocol):
    @property
    def name(self) -> OcrProviderName: ...

    reference: str
    runner_bundle_sha256: str

    def submit(self, job: OcrJob) -> str: ...

    def latest_run(self, batch_id: str) -> OcrRunStatus | None: ...

    def status(self, provider_run_id: str) -> OcrRunStatus: ...

    def logs(self, provider_run_id: str) -> str: ...

    def capacity(self) -> OcrProviderCapacity: ...

    def download_output(self, provider_run_id: str, destination: Path) -> None: ...

    def reconcile_resources(self) -> dict[str, object]: ...
