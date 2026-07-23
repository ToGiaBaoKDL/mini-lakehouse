"""Typed Kaggle adapter for versioned OCR resources and kernel runs."""

from __future__ import annotations

import json
import os
import shutil
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from mini_lakehouse.config.settings import KaggleSettings
from mini_lakehouse.contracts.processors import ProcessorContract
from mini_lakehouse.processing.ocr.kaggle_resources import (
    KaggleResourceNotFoundError,
    KaggleResourceProvisioner,
    KaggleResourceResult,
    KaggleRunnerBundle,
)
from mini_lakehouse.processing.ocr.protocol import OcrJob

_AUTH_LOCK = threading.Lock()
_MISSING = object()


class KaggleKernelState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"

    @property
    def active(self) -> bool:
        return self in {self.QUEUED, self.RUNNING}


class KaggleCommandError(RuntimeError):
    pass


class KaggleKernelNotFoundError(KaggleCommandError):
    pass


class KaggleRunStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_run_id: str
    state: KaggleKernelState
    failure_message: str | None = None


class KaggleGpuQuota(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    remaining_minutes: int = Field(ge=0)
    refresh_at: datetime | None = None


class KaggleClient(Protocol):
    kernel_slug: str
    runner_bundle_sha256: str

    def prepare_submission(self, destination: Path, job: OcrJob) -> None: ...

    def submit(self, submission_directory: Path) -> str: ...

    def latest_run(self, batch_id: str) -> KaggleRunStatus | None: ...

    def status(self, provider_run_id: str) -> KaggleRunStatus: ...

    def logs(self, provider_run_id: str) -> str: ...

    def gpu_quota(self) -> KaggleGpuQuota: ...

    def download_output(self, provider_run_id: str, destination: Path) -> None: ...


def _is_not_found(error: BaseException) -> bool:
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    error_code = getattr(error, "error_code", None)
    return status_code == 404 or error_code == 404


def parse_provider_run_id(provider_run_id: str) -> tuple[str, str, int]:
    parts = provider_run_id.split("/")
    if len(parts) != 3 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid Kaggle provider run ID {provider_run_id!r}")
    try:
        version = int(parts[2])
    except ValueError as error:
        raise ValueError(f"Invalid Kaggle provider run ID {provider_run_id!r}") from error
    if version < 1:
        raise ValueError(f"Invalid Kaggle provider run ID {provider_run_id!r}")
    return parts[0], parts[1], version


class KaggleGateway:
    """One authenticated boundary around kaggle-cli's SDK and kagglehub."""

    def __init__(self, settings: KaggleSettings) -> None:
        if not settings.configured:
            raise ValueError(
                "Kaggle OCR requires LAKEHOUSE_KAGGLE__USERNAME and LAKEHOUSE_KAGGLE__API_TOKEN"
            )
        self._settings = settings

    @contextmanager
    def _credentials(self) -> Generator[None]:
        token = self._settings.api_token
        username = self._settings.username
        if token is None or username is None:
            raise ValueError("Kaggle credentials are not configured")
        with _AUTH_LOCK:
            previous = {
                "KAGGLE_API_TOKEN": os.environ.get("KAGGLE_API_TOKEN", _MISSING),
                "KAGGLE_USERNAME": os.environ.get("KAGGLE_USERNAME", _MISSING),
            }
            os.environ["KAGGLE_API_TOKEN"] = token.get_secret_value()
            os.environ["KAGGLE_USERNAME"] = username
            try:
                yield
            finally:
                for name, value in previous.items():
                    if value is _MISSING:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = str(value)

    @staticmethod
    def _api() -> Any:
        # kaggle's package authenticates at import time; callers establish the
        # bounded credential environment before reaching this lazy import.
        from kaggle.api.kaggle_api_extended import KaggleApi

        api = KaggleApi()
        api.authenticate()
        return api

    @staticmethod
    def _split_slug(slug: str) -> tuple[str, str]:
        parts = slug.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError(f"Invalid Kaggle resource slug {slug!r}")
        return parts[0], parts[1]

    def push_kernel(
        self,
        submission_directory: Path,
        *,
        timeout_seconds: int,
        accelerator: str,
    ) -> int:
        try:
            with self._credentials():
                response = self._api().kernels_push(
                    str(submission_directory),
                    str(timeout_seconds),
                    accelerator,
                )
        except Exception as error:
            raise KaggleCommandError(f"Kaggle kernel submission failed: {error}") from error
        invalid_sources = (
            tuple(response.invalid_dataset_sources or ())
            + tuple(response.invalid_kernel_sources or ())
            + tuple(response.invalid_model_sources or ())
        )
        if response.error or invalid_sources or response.version_number < 1:
            detail = response.error or f"invalid resource sources: {', '.join(invalid_sources)}"
            raise KaggleCommandError(f"Kaggle rejected the kernel submission: {detail}")
        return int(response.version_number)

    def latest_kernel_version(self, kernel_slug: str) -> int | None:
        owner, kernel = self._split_slug(kernel_slug)
        try:
            with self._credentials():
                from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelRequest

                request = ApiGetKernelRequest()
                request.user_name = owner
                request.kernel_slug = kernel
                api = self._api()
                with api.build_kaggle_client() as client:
                    response = client.kernels.kernels_api_client.get_kernel(request)
        except Exception as error:
            if _is_not_found(error):
                return None
            raise KaggleCommandError(
                f"Cannot inspect Kaggle kernel {kernel_slug!r}: {error}"
            ) from error
        metadata = response.metadata
        version = 0 if metadata is None else int(metadata.current_version_number)
        return version or None

    def kernel_source(self, provider_run_id: str) -> str:
        owner, kernel, version = parse_provider_run_id(provider_run_id)
        try:
            with self._credentials():
                from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelRequest

                request = ApiGetKernelRequest()
                request.user_name = owner
                request.kernel_slug = kernel
                request.version_label = str(version)
                api = self._api()
                with api.build_kaggle_client() as client:
                    response = client.kernels.kernels_api_client.get_kernel(request)
        except Exception as error:
            if _is_not_found(error):
                raise KaggleKernelNotFoundError(
                    f"Kaggle run {provider_run_id!r} does not exist"
                ) from error
            raise KaggleCommandError(
                f"Cannot inspect Kaggle source for {provider_run_id!r}: {error}"
            ) from error
        if response.blob is None or not response.blob.source:
            raise KaggleCommandError(f"Kaggle run {provider_run_id!r} has no source")
        return response.blob.source

    def kernel_status(self, provider_run_id: str) -> KaggleRunStatus:
        owner, kernel, version = parse_provider_run_id(provider_run_id)
        try:
            with self._credentials():
                from kagglesdk.kernels.types.kernels_api_service import (
                    ApiGetKernelSessionStatusRequest,
                )

                request = ApiGetKernelSessionStatusRequest()
                request.user_name = owner
                request.kernel_slug = kernel
                request.version_label = str(version)
                api = self._api()
                with api.build_kaggle_client() as client:
                    response = client.kernels.kernels_api_client.get_kernel_session_status(request)
        except Exception as error:
            if _is_not_found(error):
                raise KaggleKernelNotFoundError(
                    f"Kaggle run {provider_run_id!r} does not exist"
                ) from error
            raise KaggleCommandError(
                f"Cannot inspect Kaggle run {provider_run_id!r}: {error}"
            ) from error
        raw_state = response.status.name
        if raw_state in {"QUEUED", "NEW_SCRIPT"}:
            state = KaggleKernelState.QUEUED
        elif raw_state == "RUNNING":
            state = KaggleKernelState.RUNNING
        elif raw_state == "COMPLETE":
            state = KaggleKernelState.COMPLETE
        elif raw_state in {
            "ERROR",
            "CANCEL_REQUESTED",
            "CANCEL_ACKNOWLEDGED",
        }:
            state = KaggleKernelState.FAILED
        else:
            raise KaggleCommandError(f"Unsupported Kaggle kernel state {raw_state!r}")
        return KaggleRunStatus(
            provider_run_id=provider_run_id,
            state=state,
            failure_message=response.failure_message or None,
        )

    def kernel_logs(self, provider_run_id: str) -> str:
        owner, kernel, version = parse_provider_run_id(provider_run_id)
        try:
            with self._credentials():
                from kagglesdk.kernels.types.kernels_api_service import (
                    ApiListKernelSessionOutputRequest,
                )

                request = ApiListKernelSessionOutputRequest()
                request.user_name = owner
                request.kernel_slug = kernel
                request.version_label = str(version)
                request.page_size = 1
                api = self._api()
                with api.build_kaggle_client() as client:
                    response = client.kernels.kernels_api_client.list_kernel_session_output(request)
        except Exception as error:
            if _is_not_found(error):
                raise KaggleKernelNotFoundError(
                    f"Kaggle run {provider_run_id!r} does not exist"
                ) from error
            raise KaggleCommandError(
                f"Cannot retrieve Kaggle logs for {provider_run_id!r}: {error}"
            ) from error
        return response.log

    def gpu_quota(self) -> KaggleGpuQuota:
        try:
            with self._credentials():
                response = self._api().quota_view()
        except Exception as error:
            raise KaggleCommandError(f"Cannot inspect Kaggle GPU quota: {error}") from error
        quota = response.gpu_quota
        if quota is None:
            raise KaggleCommandError("Kaggle returned no GPU quota")
        remaining = (
            (quota.total_time_allowed or timedelta())
            - (quota.time_used or timedelta())
            - (quota.time_reserved or timedelta())
        )
        refresh_at = response.quota_refresh_time
        if refresh_at is not None:
            if refresh_at.tzinfo is None:
                refresh_at = refresh_at.replace(tzinfo=UTC)
            else:
                refresh_at = refresh_at.astimezone(UTC)
        return KaggleGpuQuota(
            remaining_minutes=max(0, int(remaining.total_seconds() // 60)),
            refresh_at=refresh_at,
        )

    def download_notebook_file(
        self,
        provider_run_id: str,
        relative_path: str,
        destination: Path,
    ) -> Path:
        owner, kernel, version = parse_provider_run_id(provider_run_id)
        handle = f"{owner}/{kernel}/versions/{version}"
        try:
            with self._credentials():
                import kagglehub

                downloaded = kagglehub.notebook_output_download(
                    handle,
                    path=relative_path,
                    force_download=True,
                    output_dir=str(destination),
                )
        except Exception as error:
            if _is_not_found(error):
                raise KaggleKernelNotFoundError(
                    f"Kaggle output {relative_path!r} does not exist for {provider_run_id!r}"
                ) from error
            raise KaggleCommandError(
                f"Cannot download Kaggle output {relative_path!r} for {provider_run_id!r}: {error}"
            ) from error
        return Path(downloaded)

    def download_dataset_file(
        self,
        dataset_slug: str,
        relative_path: str,
        destination: Path,
    ) -> Path:
        try:
            with self._credentials():
                import kagglehub

                downloaded = kagglehub.dataset_download(
                    dataset_slug,
                    path=relative_path,
                    force_download=True,
                    output_dir=str(destination),
                )
        except Exception as error:
            if _is_not_found(error):
                raise KaggleResourceNotFoundError(
                    f"Kaggle Dataset {dataset_slug!r} does not exist"
                ) from error
            raise KaggleCommandError(
                f"Cannot download {relative_path!r} from Kaggle Dataset {dataset_slug!r}: {error}"
            ) from error
        return Path(downloaded)

    def upload_dataset(
        self,
        dataset_slug: str,
        source: Path,
        *,
        version_notes: str,
    ) -> None:
        try:
            with self._credentials():
                import kagglehub

                kagglehub.dataset_upload(
                    dataset_slug,
                    str(source),
                    version_notes=version_notes,
                )
        except Exception as error:
            raise KaggleCommandError(
                f"Cannot publish Kaggle runner Dataset {dataset_slug!r}: {error}"
            ) from error

    def dataset_version(self, dataset_slug: str) -> int:
        owner, dataset = self._split_slug(dataset_slug)
        try:
            with self._credentials():
                from kagglesdk.datasets.types.dataset_api_service import ApiGetDatasetRequest

                request = ApiGetDatasetRequest()
                request.owner_slug = owner
                request.dataset_slug = dataset
                api = self._api()
                with api.build_kaggle_client() as client:
                    response = client.datasets.dataset_api_client.get_dataset(request)
        except Exception as error:
            if _is_not_found(error):
                raise KaggleResourceNotFoundError(
                    f"Kaggle Dataset {dataset_slug!r} does not exist"
                ) from error
            raise KaggleCommandError(
                f"Cannot inspect Kaggle Dataset {dataset_slug!r}: {error}"
            ) from error
        return int(response.current_version_number)


def render_launcher(
    *,
    job: OcrJob,
    runner_dataset_name: str,
) -> str:
    """Render the only code file sent with a Kaggle kernel version."""
    source = f"/kaggle/input/{runner_dataset_name}"
    return (
        f"# mini-lakehouse-batch-id: {job.batch_id}\n"
        "from pathlib import Path\n"
        "import sys\n\n"
        f"SOURCE = Path({source!r})\n"
        "sys.path.insert(0, str(SOURCE))\n"
        "from bootstrap import main\n\n"
        "main(\n"
        f"    job_json={job.model_dump_json()!r},\n"
        "    source=SOURCE,\n"
        f"    expected_bundle_sha256={job.runner_bundle_sha256!r},\n"
        ")\n"
    )


class KaggleProvider:
    def __init__(
        self,
        settings: KaggleSettings,
        processor: ProcessorContract,
        *,
        gateway: KaggleGateway | None = None,
        bundle: KaggleRunnerBundle | None = None,
    ) -> None:
        if not settings.configured:
            raise ValueError(
                "Kaggle OCR requires LAKEHOUSE_KAGGLE__USERNAME and LAKEHOUSE_KAGGLE__API_TOKEN"
            )
        self._settings = settings
        self._processor = processor
        self._gateway = gateway or KaggleGateway(settings)
        self._bundle = bundle or KaggleRunnerBundle.load()
        self._resources = KaggleResourceProvisioner(
            settings,
            processor,
            self._gateway,
            bundle=self._bundle,
        )
        self.kernel_slug = settings.kernel_slug(processor.runner.kernel_name)
        self.runner_bundle_sha256 = self._bundle.sha256

    def provision_resources(self) -> KaggleResourceResult:
        return self._resources.provision()

    def prepare_submission(self, destination: Path, job: OcrJob) -> None:
        if job.runner_bundle_sha256 != self.runner_bundle_sha256:
            raise ValueError("OCR job and local Kaggle runner bundle identities differ")
        dataset_source = self._resources.resolve()
        destination.mkdir(parents=True, exist_ok=False)
        (destination / "launcher.py").write_text(
            render_launcher(
                job=job,
                runner_dataset_name=self._processor.runner.runner_dataset_name,
            ),
            encoding="utf-8",
        )
        metadata = {
            "id": self.kernel_slug,
            "title": "Mini Lakehouse ArXiv GLM OCR",
            "code_file": "launcher.py",
            "language": "python",
            "kernel_type": "script",
            "is_private": True,
            "enable_gpu": True,
            "enable_internet": True,
            "docker_image_pinning_type": "original",
            "dataset_sources": [dataset_source],
            "competition_sources": [],
            "kernel_sources": [],
            "model_sources": [],
        }
        (destination / "kernel-metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def submit(self, submission_directory: Path) -> str:
        version = self._gateway.push_kernel(
            submission_directory,
            timeout_seconds=self._processor.runner.timeout_seconds,
            accelerator=self._processor.runner.accelerator,
        )
        return f"{self.kernel_slug}/{version}"

    def latest_run(self, batch_id: str) -> KaggleRunStatus | None:
        version = self._gateway.latest_kernel_version(self.kernel_slug)
        if version is None:
            return None
        provider_run_id = f"{self.kernel_slug}/{version}"
        source = self._gateway.kernel_source(provider_run_id)
        if f"# mini-lakehouse-batch-id: {batch_id}\n" not in source:
            return None
        return self.status(provider_run_id)

    def status(self, provider_run_id: str) -> KaggleRunStatus:
        self._validate_run_id(provider_run_id)
        return self._gateway.kernel_status(provider_run_id)

    def logs(self, provider_run_id: str) -> str:
        self._validate_run_id(provider_run_id)
        return self._gateway.kernel_logs(provider_run_id)

    def gpu_quota(self) -> KaggleGpuQuota:
        return self._gateway.gpu_quota()

    def download_output(self, provider_run_id: str, destination: Path) -> None:
        self._validate_run_id(provider_run_id)
        destination.mkdir(parents=True, exist_ok=False)
        for filename in ("result_manifest.json", "result.tar.zst"):
            downloaded = self._gateway.download_notebook_file(
                provider_run_id,
                filename,
                destination,
            )
            expected = destination / filename
            if downloaded.resolve() != expected.resolve():
                shutil.copy2(downloaded, expected)

    def _validate_run_id(self, provider_run_id: str) -> None:
        owner, kernel, _ = parse_provider_run_id(provider_run_id)
        if f"{owner}/{kernel}" != self.kernel_slug:
            raise ValueError("Kaggle provider run does not belong to the configured kernel")
