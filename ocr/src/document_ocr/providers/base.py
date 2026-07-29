"""Provider-neutral remote execution contract for OCR batches."""

from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, assert_never

from pydantic import BaseModel, ConfigDict

from document_ocr.config import OcrConfig
from document_ocr.protocol import OcrJob
from document_ocr.settings import KaggleSettings, ModalSettings

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
    processor: OcrConfig,
    name: OcrProviderName,
) -> OcrProvider:
    match name:
        case "kaggle":
            from document_ocr.providers.kaggle import KaggleProvider

            return KaggleProvider(KaggleSettings(), processor)
        case "modal":
            from document_ocr.providers.modal import ModalProvider

            return ModalProvider(ModalSettings(), processor)
    assert_never(name)
