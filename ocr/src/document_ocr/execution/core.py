"""Shared execution contract and local OpenDataLoader backend."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from document_ocr.processors.opendataloader import run
from document_ocr.protocol import DocumentJob, DocumentProcessingError, OpenDataLoaderJob

type ExecutionName = Literal["modal", "oci"]
type LogSink = Callable[[str], None]
type ExecutionCheckpoint = Callable[[str], None]


class ExecutionError(RuntimeError):
    pass


class ExecutionBackend(Protocol):
    @property
    def name(self) -> ExecutionName: ...

    @property
    def reference(self) -> str: ...

    def execute(
        self,
        job: DocumentJob,
        destination: Path,
        log: LogSink,
        *,
        resume_token: str | None,
        checkpoint: ExecutionCheckpoint,
    ) -> None: ...


class LocalExecution:
    name: Literal["oci"] = "oci"
    reference = "ocr-worker/opendataloader-pdf"

    def execute(
        self,
        job: DocumentJob,
        destination: Path,
        log: LogSink,
        *,
        resume_token: str | None,
        checkpoint: ExecutionCheckpoint,
    ) -> None:
        if not isinstance(job, OpenDataLoaderJob):
            raise ExecutionError("Local execution only accepts OpenDataLoader jobs")
        if resume_token is not None and resume_token != job.run_id:
            raise ExecutionError("Persisted local execution token does not match the job")
        if resume_token is None:
            checkpoint(job.run_id)
        try:
            run(job, destination, log=log)
        except DocumentProcessingError as error:
            raise ExecutionError(str(error)) from error
