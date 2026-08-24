"""Crash-safe execution backends for local and remote document processors."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from document_ocr.errors import DocumentProcessingError
from document_ocr.protocol import DocumentJob, OcrJob, OpenDataLoaderJob
from document_ocr.providers.base import OcrLogSink, OcrProvider, OcrProviderError
from document_ocr.runners.opendataloader import run

type ExecutionBackendName = Literal["modal", "oci"]
type ExecutionCheckpoint = Callable[[str], None]


class ExecutionError(RuntimeError):
    pass


class ExecutionBackend(Protocol):
    @property
    def name(self) -> ExecutionBackendName: ...

    @property
    def reference(self) -> str: ...

    def execute(
        self,
        job: DocumentJob,
        destination: Path,
        log: OcrLogSink,
        *,
        resume_token: str | None,
        checkpoint: ExecutionCheckpoint,
    ) -> None: ...


class RemoteExecutionBackend:
    """Adapt the durable remote submit/wait/download lifecycle."""

    def __init__(self, provider: OcrProvider) -> None:
        self._provider = provider
        self._name: ExecutionBackendName = provider.name

    @property
    def name(self) -> ExecutionBackendName:
        return self._name

    @property
    def reference(self) -> str:
        return self._provider.reference

    def execute(
        self,
        job: DocumentJob,
        destination: Path,
        log: OcrLogSink,
        *,
        resume_token: str | None,
        checkpoint: ExecutionCheckpoint,
    ) -> None:
        if not isinstance(job, OcrJob):
            raise ExecutionError("Remote GPU backends only accept GLM-OCR jobs")
        try:
            token = resume_token
            if token is None:
                token = self._provider.submit(job)
                checkpoint(token)
            self._provider.wait(token, log)
            self._provider.download_output(token, destination)
        except OcrProviderError as error:
            raise ExecutionError(str(error)) from error


class OciExecutionBackend:
    """Run deterministic OpenDataLoader extraction inside the OCI worker."""

    name: Literal["oci"] = "oci"
    reference = "ocr-worker/opendataloader-pdf"

    def execute(
        self,
        job: DocumentJob,
        destination: Path,
        log: OcrLogSink,
        *,
        resume_token: str | None,
        checkpoint: ExecutionCheckpoint,
    ) -> None:
        if not isinstance(job, OpenDataLoaderJob):
            raise ExecutionError("OCI native backend only accepts OpenDataLoader jobs")
        if resume_token is not None and resume_token != job.run_id:
            raise ExecutionError("Persisted OCI execution token does not match the job")
        if resume_token is None:
            checkpoint(job.run_id)
        try:
            run(job, destination, log=log)
        except DocumentProcessingError as error:
            raise ExecutionError(str(error)) from error
