"""Execute GLM-OCR through the Modal SDK."""

import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import modal
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from document_ocr.config import GlmOcrConfig
from document_ocr.execution.core import ExecutionCheckpoint, ExecutionError, LogSink
from document_ocr.protocol import OCR_RESULT_FILES, DocumentJob, GlmOcrJob


class ModalCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token_id: SecretStr = Field(min_length=1)
    token_secret: SecretStr = Field(min_length=1)


class ModalExecution:
    name: Literal["modal"] = "modal"

    def __init__(
        self,
        credentials: ModalCredentials,
        pipeline: GlmOcrConfig,
        *,
        client: modal.Client | None = None,
    ) -> None:
        self._config = pipeline.modal
        self.reference = f"{self._config.app_name}/{self._config.function_name}"
        if client is None:
            client = modal.Client.from_credentials(
                credentials.token_id.get_secret_value(),
                credentials.token_secret.get_secret_value(),
            )
        self._client = client
        self._active_call = None

    def _call(self, run_id: str):
        try:
            call = modal.FunctionCall.from_id(run_id, client=self._client)
        except modal.exception.NotFoundError as error:
            raise ExecutionError(f"Modal function call {run_id!r} does not exist") from error
        self._active_call = call
        return call

    def _output_prefix(self, call: modal.FunctionCall[Any], run_id: str) -> str:
        try:
            value = call.get(timeout=None)
        except modal.exception.NotFoundError as error:
            raise ExecutionError(f"Modal function call {run_id!r} does not exist") from error
        except modal.exception.Error as error:
            raise ExecutionError(str(error)) from error
        except Exception as error:
            # Modal deserializes and re-raises exceptions from user code.
            raise ExecutionError(str(error)[:2000]) from error
        if not isinstance(value, str):
            raise ExecutionError("Modal OCR returned an invalid output prefix")
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or len(path.parts) != 2
            or path.parts[0] != "runs"
            or re.fullmatch(r"[0-9a-f]{64}", path.parts[1]) is None
        ):
            raise ExecutionError("Modal OCR returned an invalid output prefix")
        return value

    def _wait(self, run_id: str, log: LogSink) -> str:
        call = self._call(run_id)
        try:
            log(f"Modal function call: {call.get_dashboard_url()}\n")
            for entry in call.logs.stream():
                if entry.message:
                    log(entry.message)
            return self._output_prefix(call, run_id)
        except ExecutionError as error:
            log(f"Modal function call failed: {error}\n")
            raise
        except modal.exception.NotFoundError as error:
            raise ExecutionError(f"Modal function call {run_id!r} does not exist") from error
        except modal.exception.Error as error:
            message = f"Modal function call failed: {error}"
            log(f"{message}\n")
            raise ExecutionError(message[:2000]) from error

    def _download(self, run_id: str, output_prefix: str, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        try:
            volume = modal.Volume.from_name(
                self._config.output_volume,
                environment_name=self._config.environment,
                client=self._client,
            )
            for filename in OCR_RESULT_FILES:
                target = destination / filename
                with target.open("xb") as output:
                    for chunk in volume.read_file(f"{output_prefix}/{filename}"):
                        output.write(chunk)
        except (OSError, modal.exception.Error) as error:
            raise ExecutionError(f"Cannot download Modal output for {run_id}: {error}") from error

    def execute(
        self,
        job: DocumentJob,
        destination: Path,
        log: LogSink,
        *,
        resume_token: str | None,
        checkpoint: ExecutionCheckpoint,
    ) -> None:
        if not isinstance(job, GlmOcrJob):
            raise ExecutionError("Modal execution only accepts GLM-OCR jobs")
        run_id = resume_token
        if run_id is None:
            function = modal.Function.from_name(
                self._config.app_name,
                self._config.function_name,
                environment_name=self._config.environment,
                client=self._client,
            )
            call = function.spawn(job.model_dump_json())
            self._active_call = call
            run_id = call.object_id
            checkpoint(run_id)
        output_prefix = self._wait(run_id, log)
        self._download(run_id, output_prefix, destination)

    def cancel(self) -> None:
        """Cancel the active Modal call and release its GPU container."""
        if self._active_call is None:
            return
        try:
            self._active_call.cancel(terminate_containers=True)
        except modal.exception.Error as error:
            raise ExecutionError(
                f"Cannot cancel the active Modal function call: {error}"
            ) from error
