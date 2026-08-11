"""Kaggle execution adapter backed only by the public Kaggle API."""

import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from document_ocr.config import GlmOcrConfig
from document_ocr.output import OCR_ARCHIVE_FILE, OCR_RESULT_FILE
from document_ocr.protocol import OcrJob, OcrRunResult
from document_ocr.providers.base import (
    OcrLogSink,
    OcrProviderError,
    OcrProviderRunFailedError,
    OcrRunNotFoundError,
)
from document_ocr.settings import KaggleSettings


def render_launcher(
    *,
    job: OcrJob,
    runner_dataset_source: str,
    model_source: str,
    layout_model_source: str,
) -> str:
    return (
        "from pathlib import Path\n"
        "import sys\n\n"
        "import kagglehub\n\n"
        f"SOURCE = Path(kagglehub.dataset_download({runner_dataset_source!r}))\n"
        f"MODEL = Path(kagglehub.model_download({model_source!r}))\n"
        f"LAYOUT_MODEL = Path(kagglehub.model_download({layout_model_source!r}))\n"
        "sys.path.insert(0, str(SOURCE))\n"
        "from bootstrap import main\n\n"
        "main(\n"
        f"    job_json={job.model_dump_json()!r},\n"
        "    source=SOURCE,\n"
        "    model_path=MODEL,\n"
        "    layout_model_path=LAYOUT_MODEL,\n"
        ")\n"
    )


class KaggleProvider:
    name: Literal["kaggle"] = "kaggle"

    def __init__(
        self,
        settings: KaggleSettings,
        processor: GlmOcrConfig,
        *,
        api: Any | None = None,
    ) -> None:
        if api is None:
            from kaggle.api.kaggle_api_extended import KaggleApi

            api = KaggleApi()
            api.authenticate()
        runner = processor.runner.kaggle
        self._runner = runner
        self._api = api
        self.reference = f"{settings.username}/{runner.kernel_name}"
        dataset = f"{settings.username}/{runner.runner_dataset_name}"
        self._runner_dataset_handle = f"{dataset}/versions/{runner.runner_dataset_version}"
        self._runner_dataset_attachment = f"{dataset}/{runner.runner_dataset_version}"

    def submit(self, job: OcrJob) -> str:
        try:
            current = self._status(self.reference)
        except OcrRunNotFoundError:
            current = None
        if current is not None and current[0] in {"QUEUED", "NEW_SCRIPT", "RUNNING"}:
            raise OcrProviderError(
                f"Kaggle kernel {self.reference!r} already has an active OCR run"
            )
        if (
            current is not None
            and current[0] == "COMPLETE"
            and self._completed_run_id() == job.run_id
        ):
            return self.reference

        with TemporaryDirectory(prefix="arxiv-ocr-kaggle-") as temporary_directory:
            submission = Path(temporary_directory)
            (submission / "launcher.py").write_text(
                render_launcher(
                    job=job,
                    runner_dataset_source=self._runner_dataset_handle,
                    model_source=self._runner.model_source,
                    layout_model_source=self._runner.layout_model_source,
                ),
                encoding="utf-8",
            )
            metadata = {
                "id": self.reference,
                "title": "ArXiv GLM-OCR",
                "code_file": "launcher.py",
                "language": "python",
                "kernel_type": "script",
                "is_private": True,
                "enable_gpu": True,
                "enable_internet": True,
                "docker_image_pinning_type": "original",
                "dataset_sources": [self._runner_dataset_attachment],
                "competition_sources": [],
                "kernel_sources": [],
                "model_sources": [
                    self._runner.model_source,
                    self._runner.layout_model_source,
                ],
            }
            (submission / "kernel-metadata.json").write_text(
                json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            try:
                response = self._api.kernels_push(
                    str(submission),
                    str(self._runner.timeout_seconds),
                    self._runner.accelerator,
                )
            except Exception as error:
                raise OcrProviderError(f"Kaggle kernel submission failed: {error}") from error
            invalid_sources = (
                tuple(response.invalid_dataset_sources or ())
                + tuple(response.invalid_kernel_sources or ())
                + tuple(response.invalid_model_sources or ())
            )
            if response.error or invalid_sources:
                detail = response.error or (
                    f"invalid resource sources: {', '.join(invalid_sources)}"
                )
                raise OcrProviderError(f"Kaggle rejected the kernel submission: {detail}")
        return self.reference

    def _completed_run_id(self) -> str:
        with TemporaryDirectory(prefix="document-ocr-kaggle-output-") as temporary_directory:
            destination = Path(temporary_directory)
            try:
                self._api.kernels_output(
                    self.reference,
                    str(destination),
                    file_pattern=rf"^{re.escape(OCR_RESULT_FILE)}$",
                    force=True,
                    quiet=True,
                )
                manifest = OcrRunResult.model_validate_json(
                    (destination / OCR_RESULT_FILE).read_bytes()
                )
            except Exception as error:
                raise OcrProviderError(
                    f"Cannot validate completed Kaggle output for {self.reference!r}: {error}"
                ) from error
            return manifest.run_id

    def _status(self, provider_run_id: str) -> tuple[str, str | None]:
        self._validate_run_id(provider_run_id)
        try:
            response = self._api.kernels_status(provider_run_id)
        except Exception as error:
            if _status_code(error) == 404:
                raise OcrRunNotFoundError(
                    f"Kaggle kernel {provider_run_id!r} does not exist"
                ) from error
            raise OcrProviderError(
                f"Cannot inspect Kaggle kernel {provider_run_id!r}: {error}"
            ) from error
        supported_states = {
            "QUEUED",
            "NEW_SCRIPT",
            "RUNNING",
            "COMPLETE",
            "ERROR",
            "CANCEL_REQUESTED",
            "CANCEL_ACKNOWLEDGED",
        }
        raw_state = response.status.name
        if raw_state not in supported_states:
            raise OcrProviderError(f"Unsupported Kaggle kernel state {raw_state!r}")
        return raw_state, response.failure_message or None

    def wait(
        self,
        provider_run_id: str,
        log: OcrLogSink,
    ) -> None:
        self._validate_run_id(provider_run_id)
        while True:
            try:
                for event in self._api.kernels_logs_stream(provider_run_id):
                    message = event.get("data")
                    if isinstance(message, str) and message:
                        log(message)
            except Exception as error:
                if _status_code(error) == 404:
                    raise OcrRunNotFoundError(
                        f"Kaggle kernel {provider_run_id!r} does not exist"
                    ) from error
                raise OcrProviderError(
                    f"Cannot stream Kaggle logs for {provider_run_id!r}: {error}"
                ) from error
            state, failure_message = self._status(provider_run_id)
            if state in {"QUEUED", "NEW_SCRIPT", "RUNNING"}:
                continue
            if state == "COMPLETE":
                return
            raise OcrProviderRunFailedError(
                failure_message or f"Kaggle kernel ended in state {state}"
            )

    def download_output(self, provider_run_id: str, destination: Path) -> None:
        self._validate_run_id(provider_run_id)
        destination.mkdir(parents=True, exist_ok=False)
        try:
            self._api.kernels_output(
                provider_run_id,
                str(destination),
                file_pattern=(rf"^(?:{re.escape(OCR_RESULT_FILE)}|{re.escape(OCR_ARCHIVE_FILE)})$"),
                force=True,
                quiet=True,
            )
        except Exception as error:
            raise OcrProviderError(
                f"Cannot download Kaggle output for {provider_run_id!r}: {error}"
            ) from error

    def _validate_run_id(self, provider_run_id: str) -> None:
        if provider_run_id != self.reference:
            raise ValueError("Kaggle provider run does not belong to the configured kernel")


def _status_code(error: BaseException) -> int | None:
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None
