"""Kaggle execution adapter backed only by the public Kaggle API."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from document_ocr.config import OcrConfig
from document_ocr.protocol import OcrJob
from document_ocr.providers.base import (
    OCR_RESULT_FILES,
    OcrProviderError,
    OcrProviderState,
    OcrRunNotFoundError,
    OcrRunStatus,
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
        processor: OcrConfig,
        *,
        api: Any | None = None,
    ) -> None:
        if not settings.configured:
            raise ValueError("Kaggle OCR requires KAGGLE_USERNAME and KAGGLE_API_TOKEN")
        assert settings.username is not None
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

    def status(self, provider_run_id: str) -> OcrRunStatus:
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
        states = {
            "QUEUED": OcrProviderState.QUEUED,
            "NEW_SCRIPT": OcrProviderState.QUEUED,
            "RUNNING": OcrProviderState.RUNNING,
            "COMPLETE": OcrProviderState.COMPLETE,
            "ERROR": OcrProviderState.FAILED,
            "CANCEL_REQUESTED": OcrProviderState.FAILED,
            "CANCEL_ACKNOWLEDGED": OcrProviderState.FAILED,
        }
        raw_state = response.status.name
        try:
            state = states[raw_state]
        except KeyError as error:
            raise OcrProviderError(f"Unsupported Kaggle kernel state {raw_state!r}") from error
        return OcrRunStatus(
            provider_run_id=provider_run_id,
            state=state,
            failure_message=response.failure_message or None,
        )

    def logs(self, provider_run_id: str) -> str:
        self._validate_run_id(provider_run_id)
        try:
            return self._api.kernels_logs(provider_run_id)
        except Exception as error:
            raise OcrProviderError(
                f"Cannot read Kaggle logs for {provider_run_id!r}: {error}"
            ) from error

    def download_output(self, provider_run_id: str, destination: Path) -> None:
        self._validate_run_id(provider_run_id)
        destination.mkdir(parents=True, exist_ok=False)
        try:
            self._api.kernels_output(
                provider_run_id,
                str(destination),
                file_pattern=r"^result(?:_manifest\.json|\.tar\.zst)$",
                force=True,
                quiet=True,
            )
        except Exception as error:
            raise OcrProviderError(
                f"Cannot download Kaggle output for {provider_run_id!r}: {error}"
            ) from error
        missing = sorted(name for name in OCR_RESULT_FILES if not (destination / name).is_file())
        if missing:
            raise OcrProviderError(f"Kaggle output is missing: {', '.join(missing)}")

    def _validate_run_id(self, provider_run_id: str) -> None:
        if provider_run_id != self.reference:
            raise ValueError("Kaggle provider run does not belong to the configured kernel")


def _status_code(error: BaseException) -> int | None:
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None
