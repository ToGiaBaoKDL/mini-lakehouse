"""Provider-neutral remote execution contract for OCR runs."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from document_ocr.protocol import OcrJob

type OcrProviderName = Literal["kaggle", "modal"]
type OcrLogSink = Callable[[str], None]


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
