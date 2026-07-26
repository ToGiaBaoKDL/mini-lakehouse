"""Shared types and ports for the Kaggle OCR adapter."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mini_lakehouse.processing.ocr.core.protocol import OcrJob

KaggleResourceName = Literal["runner", "model", "layout_model"]
KaggleResourceKind = Literal["dataset", "model"]
KaggleResourceAction = Literal["created", "updated", "unchanged"]


class KaggleKernelState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"

    @property
    def active(self) -> bool:
        return self in {self.QUEUED, self.RUNNING}


class KaggleCommandError(RuntimeError):
    pass


class KaggleKernelNotFoundError(KaggleCommandError):
    pass


class KaggleResourceNotFoundError(RuntimeError):
    pass


class KaggleResourceDriftError(RuntimeError):
    pass


class KaggleRunStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_run_id: str
    state: KaggleKernelState
    failure_message: str | None = None


class KaggleCurrentRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: KaggleRunStatus
    source: str


class KaggleGpuQuota(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    remaining_minutes: int = Field(ge=0)
    refresh_at: datetime | None = None


class KaggleClient(Protocol):
    kernel_slug: str
    runner_bundle_sha256: str

    def prepare_submission(self, destination: Path, job: OcrJob) -> None: ...

    def submit(self, submission_directory: Path) -> str: ...

    def latest_run(self, batch_id: str) -> KaggleRunStatus | None: ...

    def status(self, provider_run_id: str) -> KaggleRunStatus: ...

    def logs(self, provider_run_id: str) -> str: ...

    def stream_logs(self, provider_run_id: str) -> Iterator[str]: ...

    def gpu_quota(self) -> KaggleGpuQuota: ...

    def download_output(self, provider_run_id: str, destination: Path) -> None: ...


class KaggleResourceClient(Protocol):
    def download_dataset_file(
        self,
        dataset_slug: str,
        relative_path: str,
        destination: Path,
    ) -> Path: ...

    def upload_dataset(
        self,
        dataset_slug: str,
        source: Path,
        *,
        version_notes: str,
    ) -> None: ...

    def dataset_version(self, dataset_slug: str) -> int: ...

    def download_model_file(
        self,
        model_slug: str,
        relative_path: str,
        destination: Path,
    ) -> Path: ...

    def upload_model(
        self,
        model_slug: str,
        source: Path,
        *,
        license_name: str,
        version_notes: str,
    ) -> None: ...

    def model_version(self, model_slug: str) -> int: ...


class ModelSnapshotClient(Protocol):
    def download(
        self,
        repository: str,
        revision: str,
        destination: Path,
    ) -> None: ...


class KaggleResourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: KaggleResourceName
    kind: KaggleResourceKind
    source: str
    identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class KaggleResourceResult(KaggleResourceReference):
    action: KaggleResourceAction


class KaggleOcrResourceReferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    runner: KaggleResourceReference
    model: KaggleResourceReference
    layout_model: KaggleResourceReference

    @model_validator(mode="after")
    def validate_roles(self) -> KaggleOcrResourceReferences:
        if (
            self.runner.name != "runner"
            or self.runner.kind != "dataset"
            or self.model.name != "model"
            or self.model.kind != "model"
            or self.layout_model.name != "layout_model"
            or self.layout_model.kind != "model"
        ):
            raise ValueError("Kaggle OCR resource references do not match their roles")
        return self

    @property
    def model_sources(self) -> list[str]:
        return [self.model.source, self.layout_model.source]


class ManagedKaggleResource(Protocol):
    @property
    def name(self) -> KaggleResourceName: ...

    @property
    def kind(self) -> KaggleResourceKind: ...

    @property
    def identity_sha256(self) -> str: ...

    @property
    def unversioned_source(self) -> str: ...

    def remote_identity(self) -> str | None: ...

    def publish(self) -> None: ...

    def current_version(self) -> int: ...

    def versioned_source(self, version: int) -> str: ...


def parse_provider_run_id(provider_run_id: str) -> tuple[str, str, int]:
    parts = provider_run_id.split("/")
    if len(parts) != 3 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid Kaggle provider run ID {provider_run_id!r}")
    try:
        version = int(parts[2])
    except ValueError as error:
        raise ValueError(f"Invalid Kaggle provider run ID {provider_run_id!r}") from error
    if version < 1:
        raise ValueError(f"Invalid Kaggle provider run ID {provider_run_id!r}")
    return parts[0], parts[1], version
