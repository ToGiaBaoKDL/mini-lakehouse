"""Provider-neutral remote execution contract for OCR batches."""

from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, assert_never

from pydantic import BaseModel, ConfigDict

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts.processors import ProcessorContract
from mini_lakehouse.processing.ocr.core.protocol import OcrJob

type OcrProviderName = Literal["kaggle", "modal"]
OCR_RESULT_FILES = ("result_manifest.json", "result.tar.zst")


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


class OcrProvider(Protocol):
    @property
    def name(self) -> OcrProviderName: ...

    reference: str

    def submit(self, job: OcrJob) -> str: ...

    def status(self, provider_run_id: str) -> OcrRunStatus: ...

    def logs(self, provider_run_id: str) -> str: ...

    def download_output(self, provider_run_id: str, destination: Path) -> None: ...


def create_ocr_provider(
    settings: Settings,
    processor: ProcessorContract,
    name: OcrProviderName,
) -> OcrProvider:
    match name:
        case "kaggle":
            from mini_lakehouse.processing.ocr.kaggle_provider import KaggleProvider

            return KaggleProvider(settings.kaggle, processor)
        case "modal":
            from mini_lakehouse.processing.ocr.modal_provider import ModalProvider

            return ModalProvider(settings.modal, processor)
    assert_never(name)
