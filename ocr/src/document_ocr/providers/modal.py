"""Modal execution adapter backed by the public Modal SDK."""

from pathlib import Path
from typing import Literal

import modal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from document_ocr.archive import OCR_RESULT_FILES
from document_ocr.config import OcrConfig
from document_ocr.protocol import OcrJob
from document_ocr.providers.base import (
    OcrLogSink,
    OcrProviderError,
    OcrProviderRunFailedError,
    OcrRunNotFoundError,
)
from document_ocr.settings import ModalSettings


class ModalRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_prefix: str
    state: Literal["complete"]


class ModalProvider:
    name: Literal["modal"] = "modal"

    def __init__(
        self,
        settings: ModalSettings,
        processor: OcrConfig,
        *,
        client: modal.Client | None = None,
    ) -> None:
        runner = processor.runner.modal
        self._runner = runner
        self.reference = f"{runner.app_name}/{runner.function_name}"
        if client is None:
            client = modal.Client.from_credentials(
                settings.token_id.get_secret_value(),
                settings.token_secret.get_secret_value(),
            )
        self._client = client

    def _function(self, name: str):
        return modal.Function.from_name(
            self._runner.app_name,
            name,
            environment_name=self._runner.environment,
            client=self._client,
        )

    def _call(self, provider_run_id: str):
        try:
            return modal.FunctionCall.from_id(provider_run_id, client=self._client)
        except modal.exception.NotFoundError as error:
            raise OcrRunNotFoundError(
                f"Modal function call {provider_run_id!r} does not exist"
            ) from error

    def submit(self, job: OcrJob) -> str:
        call = self._function(self._runner.function_name).spawn(job.model_dump_json())
        return call.object_id

    def _result(self, provider_run_id: str, *, timeout: float | None) -> ModalRunResult:
        call = self._call(provider_run_id)
        try:
            value = call.get(timeout=timeout)
        except modal.exception.TimeoutError:
            raise
        except modal.exception.NotFoundError as error:
            raise OcrRunNotFoundError(
                f"Modal function call {provider_run_id!r} does not exist"
            ) from error
        except modal.exception.Error as error:
            raise OcrProviderError(str(error)) from error
        try:
            return ModalRunResult.model_validate(value)
        except ValidationError as error:
            raise OcrProviderError("Modal OCR returned an invalid result") from error

    def wait(
        self,
        provider_run_id: str,
        log: OcrLogSink,
    ) -> None:
        call = self._call(provider_run_id)
        try:
            for entry in call.logs.stream():
                if entry.message:
                    log(entry.message)
            self._result(provider_run_id, timeout=None)
        except modal.exception.NotFoundError as error:
            raise OcrRunNotFoundError(
                f"Modal function call {provider_run_id!r} does not exist"
            ) from error
        except modal.exception.Error as error:
            raise OcrProviderError(f"Cannot stream Modal logs: {error}") from error
        except OcrRunNotFoundError:
            raise
        except OcrProviderError as error:
            raise OcrProviderRunFailedError(str(error)[:2000]) from error

    def download_output(self, provider_run_id: str, destination: Path) -> None:
        result = self._result(provider_run_id, timeout=None)
        destination.mkdir(parents=True, exist_ok=False)
        try:
            volume = modal.Volume.from_name(
                self._runner.output_volume,
                environment_name=self._runner.environment,
                client=self._client,
            )
            for filename in OCR_RESULT_FILES:
                target = destination / filename
                with target.open("xb") as output:
                    for chunk in volume.read_file(f"{result.output_prefix}/{filename}"):
                        output.write(chunk)
        except (OSError, modal.exception.Error) as error:
            raise OcrProviderError(
                f"Cannot download Modal output for {result.run_id}: {error}"
            ) from error
