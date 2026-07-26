"""Authenticated Kaggle SDK and KaggleHub boundary."""

from __future__ import annotations

import os
import threading
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

from mini_lakehouse.config.settings import KaggleSettings
from mini_lakehouse.processing.ocr.kaggle_types import (
    KaggleCommandError,
    KaggleCurrentRun,
    KaggleGpuQuota,
    KaggleKernelNotFoundError,
    KaggleKernelState,
    KaggleResourceNotFoundError,
    KaggleRunStatus,
    parse_provider_run_id,
)

_AUTH_LOCK = threading.Lock()
_MISSING = object()


def _status_code(error: BaseException) -> int | None:
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    error_code = getattr(error, "error_code", None)
    return status_code if isinstance(status_code, int) else error_code


def _is_not_found(error: BaseException) -> bool:
    return _status_code(error) == 404


def _is_private_resource_missing(error: BaseException) -> bool:
    # Kaggle deliberately returns 403 for a private resource that is not visible
    # yet, including a resource owned by the authenticated account.
    return _status_code(error) in {403, 404}


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

    @staticmethod
    def _downloaded_file(
        downloaded: str,
        destination: Path,
        relative_path: str,
        *,
        resource: str,
    ) -> Path:
        actual = Path(downloaded)
        expected = destination / relative_path
        if actual.resolve() != expected.resolve() or not expected.is_file():
            raise KaggleCommandError(
                f"KaggleHub returned an unexpected path for {resource}: {actual}"
            )
        return expected

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

    def _current_kernel(self, kernel_slug: str) -> Any | None:
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
            if _is_private_resource_missing(error):
                return None
            raise KaggleCommandError(
                f"Cannot inspect Kaggle kernel {kernel_slug!r}: {error}"
            ) from error
        return response

    def current_kernel_run(self, kernel_slug: str) -> KaggleCurrentRun | None:
        response = self._current_kernel(kernel_slug)
        if response is None:
            return None
        metadata = response.metadata
        version = 0 if metadata is None else int(metadata.current_version_number)
        if version < 1:
            return None
        if response.blob is None or not response.blob.source:
            raise KaggleCommandError(f"Kaggle kernel {kernel_slug!r} has no source")
        owner, kernel = self._split_slug(kernel_slug)
        provider_run_id = f"{kernel_slug}/{version}"
        return KaggleCurrentRun(
            status=self._read_kernel_status(owner, kernel, provider_run_id),
            source=response.blob.source,
        )

    def kernel_status(self, provider_run_id: str) -> KaggleRunStatus:
        owner, kernel, version = parse_provider_run_id(provider_run_id)
        self._require_current_kernel_version(owner, kernel, version)
        return self._read_kernel_status(owner, kernel, provider_run_id)

    def _read_kernel_status(
        self,
        owner: str,
        kernel: str,
        provider_run_id: str,
    ) -> KaggleRunStatus:
        try:
            with self._credentials():
                from kagglesdk.kernels.types.kernels_api_service import (
                    ApiGetKernelSessionStatusRequest,
                )

                request = ApiGetKernelSessionStatusRequest()
                request.user_name = owner
                request.kernel_slug = kernel
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

    def stream_kernel_logs(self, provider_run_id: str) -> Iterator[str]:
        """Yield the official latest-session log stream without blocking status calls."""
        owner, kernel, version = parse_provider_run_id(provider_run_id)
        self._require_current_kernel_version(owner, kernel, version)
        try:
            with self._credentials():
                api = self._api()
            # Do not hold _AUTH_LOCK while consuming the long-lived SSE
            # response. The authenticated client already owns its credentials,
            # and status polling must remain independently available.
            for event in api.kernels_logs_stream(f"{owner}/{kernel}"):
                data = event.get("data")
                if isinstance(data, str) and data:
                    yield data
        except Exception as error:
            if _is_not_found(error):
                raise KaggleKernelNotFoundError(
                    f"Kaggle run {provider_run_id!r} does not exist"
                ) from error
            raise KaggleCommandError(
                f"Cannot stream Kaggle logs for {provider_run_id!r}: {error}"
            ) from error

    def kernel_logs(self, provider_run_id: str) -> str:
        chunks: list[str] = []
        for chunk in self.stream_kernel_logs(provider_run_id):
            chunks.append(chunk)
            if not chunk.endswith("\n"):
                chunks.append("\n")
        return "".join(chunks)

    def _require_current_kernel_version(self, owner: str, kernel: str, version: int) -> None:
        """Guard latest-only Kaggle status/log endpoints with the persisted run version."""
        kernel_slug = f"{owner}/{kernel}"
        response = self._current_kernel(kernel_slug)
        metadata = None if response is None else response.metadata
        current_version = 0 if metadata is None else int(metadata.current_version_number)
        if current_version != version:
            raise KaggleKernelNotFoundError(
                f"Kaggle run {kernel_slug}/{version!s} is not the current kernel version"
            )

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
        return self._downloaded_file(
            downloaded,
            destination,
            relative_path,
            resource=f"notebook output {provider_run_id!r}",
        )

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
        return self._downloaded_file(
            downloaded,
            destination,
            relative_path,
            resource=f"Dataset {dataset_slug!r}",
        )

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
            if _is_private_resource_missing(error):
                raise KaggleResourceNotFoundError(
                    f"Kaggle Dataset {dataset_slug!r} does not exist"
                ) from error
            raise KaggleCommandError(
                f"Cannot inspect Kaggle Dataset {dataset_slug!r}: {error}"
            ) from error
        return int(response.current_version_number)

    def download_model_file(
        self,
        model_slug: str,
        relative_path: str,
        destination: Path,
    ) -> Path:
        try:
            with self._credentials():
                import kagglehub

                downloaded = kagglehub.model_download(
                    model_slug,
                    path=relative_path,
                    force_download=True,
                    output_dir=str(destination),
                )
        except Exception as error:
            if _is_not_found(error):
                raise KaggleResourceNotFoundError(
                    f"Kaggle model {model_slug!r} does not exist"
                ) from error
            raise KaggleCommandError(
                f"Cannot download {relative_path!r} from Kaggle model {model_slug!r}: {error}"
            ) from error
        return self._downloaded_file(
            downloaded,
            destination,
            relative_path,
            resource=f"model {model_slug!r}",
        )

    def upload_model(
        self,
        model_slug: str,
        source: Path,
        *,
        license_name: str,
        version_notes: str,
    ) -> None:
        try:
            with self._credentials():
                import kagglehub

                kagglehub.model_upload(
                    model_slug,
                    str(source),
                    license_name=license_name,
                    version_notes=version_notes,
                )
        except Exception as error:
            raise KaggleCommandError(
                f"Cannot publish Kaggle model {model_slug!r}: {error}"
            ) from error

    def model_version(self, model_slug: str) -> int:
        try:
            with self._credentials():
                response = self._api().model_instance_get(model_slug)
        except Exception as error:
            if _is_private_resource_missing(error):
                raise KaggleResourceNotFoundError(
                    f"Kaggle model {model_slug!r} does not exist"
                ) from error
            raise KaggleCommandError(
                f"Cannot inspect Kaggle model {model_slug!r}: {error}"
            ) from error
        return int(response.version_number)
