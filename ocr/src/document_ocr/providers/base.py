"""Provider-neutral remote execution contract for OCR runs."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from document_ocr.protocol import OcrJob

type OcrProviderName = Literal["kaggle", "modal"]
type OcrLogSink = Callable[[str], None]


def serialize_glm_runner_job(job: OcrJob) -> str:
    """Serialize the stable remote-runner payload without orchestration-only fields."""
    return job.model_dump_json(exclude={"adapter"})


class OcrProviderError(RuntimeError):
    pass


class OcrProviderRunFailedError(OcrProviderError):
    pass


class OcrRunNotFoundError(OcrProviderError):
    pass


class OcrProvider(Protocol):
    @property
    def name(self) -> OcrProviderName: ...

    reference: str

    def submit(self, job: OcrJob) -> str: ...

    def wait(
        self,
        provider_run_id: str,
        log: OcrLogSink,
    ) -> None: ...

    def download_output(self, provider_run_id: str, destination: Path) -> None: ...
