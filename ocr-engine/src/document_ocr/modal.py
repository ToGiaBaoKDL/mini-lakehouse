"""Run one OCR job through the Modal SDK."""

import re
from pathlib import Path, PurePosixPath
from typing import Any

import modal
from loguru import logger

from document_ocr.config import OcrConfig
from document_ocr.protocol import OCR_RESULT_FILES, OcrError, OcrJob

_REMOTE_CLASS = "Ocr"
_REMOTE_METHOD = "run"


class ModalOcr:
    def __init__(
        self,
        *,
        token_id: str,
        token_secret: str,
        config: OcrConfig,
        client: modal.Client | None = None,
    ) -> None:
        self._config = config.modal
        self._client = client or modal.Client.from_credentials(token_id, token_secret)
        self._active_call: modal.FunctionCall[Any] | None = None

    def run(
        self,
        job: OcrJob,
        destination: Path,
    ) -> None:
        remote_class = modal.Cls.from_name(
            self._config.app_name,
            _REMOTE_CLASS,
            environment_name=self._config.environment,
            client=self._client,
        )
        method = getattr(remote_class(), _REMOTE_METHOD)
        call = method.spawn(job.model_dump_json())
        self._active_call = call
        call_id = call.object_id
        logger.info("Modal function call: {}", call.get_dashboard_url())
        try:
            for entry in call.logs.stream():
                if entry.message:
                    print(entry.message, end="", flush=True)
            output_prefix = call.get(timeout=None)
            if not isinstance(output_prefix, str):
                raise OcrError("Modal OCR returned an invalid output prefix")
            path = PurePosixPath(output_prefix)
            if (
                path.is_absolute()
                or len(path.parts) != 2
                or path.parts[0] != "jobs"
                or re.fullmatch(r"[0-9a-f]{64}", path.parts[1]) is None
            ):
                raise OcrError("Modal OCR returned an invalid output prefix")
            destination.mkdir(parents=True, exist_ok=False)
            volume = modal.Volume.from_name(
                self._config.output_volume,
                environment_name=self._config.environment,
                client=self._client,
            )
            for filename in OCR_RESULT_FILES:
                with (destination / filename).open("xb") as output:
                    for chunk in volume.read_file(f"{output_prefix}/{filename}"):
                        output.write(chunk)
        except OcrError:
            raise
        except (OSError, modal.exception.Error) as error:
            raise OcrError(f"Modal OCR call {call_id} failed: {error}") from error
        except Exception as error:
            # Modal deserializes and re-raises user-code exceptions.
            raise OcrError(str(error)[:2000]) from error

    def cancel(self) -> None:
        if self._active_call is None:
            return
        try:
            self._active_call.cancel(terminate_containers=True)
        except modal.exception.Error as error:
            raise OcrError(f"Cannot cancel the active Modal call: {error}") from error
