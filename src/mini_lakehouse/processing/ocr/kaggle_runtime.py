"""Versioned, offline uv cache used by the Kaggle OCR runner."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mini_lakehouse.processing.ocr.core.files import file_sha256
from mini_lakehouse.processing.ocr.core.identity import canonical_json_sha256
from mini_lakehouse.processing.ocr.kaggle_types import (
    KaggleResourceClient,
    KaggleResourceDriftError,
    KaggleResourceNotFoundError,
)

RUNTIME_MANIFEST_NAME = "runtime_manifest.json"
RUNTIME_CACHE_ARCHIVE_NAME = "uv-cache.zip"
RUNTIME_PYTHON = "3.12"
RUNTIME_PYTHON_ABI = "cp312"
RUNTIME_PLATFORM = "x86_64-manylinux_2_28"
RUNTIME_UV_VERSION = "0.11.30"


class RuntimeResourceManifest(BaseModel):
    """Identity and integrity metadata for one immutable dependency cache."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0.0"]
    resource_name: Literal["runtime"]
    project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    python: Literal["3.12"]
    python_abi: Literal["cp312"]
    platform: Literal["x86_64-manylinux_2_28"]
    uv_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    cache_archive: Literal["uv-cache.zip"]
    cache_archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def identity_payload(self) -> dict[str, str]:
        return {
            "lock_sha256": self.lock_sha256,
            "platform": self.platform,
            "project_sha256": self.project_sha256,
            "python": self.python,
            "python_abi": self.python_abi,
            "resource_name": self.resource_name,
            "uv_version": self.uv_version,
        }

    @model_validator(mode="after")
    def validate_identity(self) -> RuntimeResourceManifest:
        if canonical_json_sha256(self.identity_payload()) != self.identity_sha256:
            raise ValueError("Runtime resource identity does not match its dependency inputs")
        return self


class RuntimeCacheBuilder(Protocol):
    def build(self, destination: Path) -> None: ...


class UvRuntimeCacheBuilder:
    """Populate and verify the exact Linux/Python uv cache before publication."""

    def __init__(
        self,
        *,
        runner_source: Path = Path("runners/kaggle/glm_ocr"),
        uv_executable: str | None = None,
    ) -> None:
        self._runner_source = runner_source
        resolved_uv = uv_executable or shutil.which("uv")
        if resolved_uv is None:
            raise RuntimeError(
                "uv is required to provision the Kaggle OCR runtime dependency cache"
            )
        self._uv: str = resolved_uv

    def build(self, destination: Path) -> None:
        completed = subprocess.run(
            [self._uv, "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
        version_parts = completed.stdout.strip().split()
        actual_version = (
            version_parts[1] if len(version_parts) >= 2 and version_parts[0] == "uv" else None
        )
        if actual_version != RUNTIME_UV_VERSION:
            raise RuntimeError(
                f"Kaggle OCR runtime must be provisioned with uv {RUNTIME_UV_VERSION}, "
                f"found {completed.stdout.strip()!r}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="kaggle-ocr-runtime-build-") as temporary_directory:
            temporary = Path(temporary_directory)
            project = temporary / "project"
            cache = temporary / "cache"
            project.mkdir()
            for name in ("pyproject.toml", "uv.lock"):
                shutil.copy2(self._runner_source / name, project / name)
            environment = os.environ.copy()
            environment["UV_CACHE_DIR"] = str(cache)
            environment["UV_LINK_MODE"] = "hardlink"
            environment["UV_PROJECT_ENVIRONMENT"] = str(temporary / "populate-venv")
            self._sync(project, environment)
            self._prune(cache, environment)
            shutil.rmtree(environment["UV_PROJECT_ENVIRONMENT"], ignore_errors=True)

            # Prove that the published cache is complete. Runtime bootstrap has no
            # network fallback: an incomplete resource must fail during governance.
            environment["UV_PROJECT_ENVIRONMENT"] = str(temporary / "verify-venv")
            self._sync(project, environment, offline=True)
            shutil.rmtree(environment["UV_PROJECT_ENVIRONMENT"], ignore_errors=True)
            self._prune(cache, environment)
            self._write_cache_archive(cache, destination)

    def _sync(
        self,
        project: Path,
        environment: dict[str, str],
        *,
        offline: bool = False,
    ) -> None:
        command = [
            self._uv,
            "sync",
            "--project",
            str(project),
            "--frozen",
            "--no-dev",
            "--python",
            RUNTIME_PYTHON,
            "--python-platform",
            RUNTIME_PLATFORM,
            "--link-mode",
            "hardlink",
            "--no-progress",
        ]
        if offline:
            command.append("--offline")
        subprocess.run(command, check=True, env=environment)

    def _prune(self, cache: Path, environment: dict[str, str]) -> None:
        subprocess.run(
            [self._uv, "cache", "prune", "--ci", "--cache-dir", str(cache)],
            check=True,
            env=environment,
        )

    @staticmethod
    def _write_cache_archive(cache: Path, destination: Path) -> None:
        with zipfile.ZipFile(
            destination,
            mode="x",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as archive:
            for path in sorted(item for item in cache.rglob("*") if item.is_file()):
                archive.write(path, path.relative_to(cache).as_posix())


class KaggleRuntimeDatasetResource:
    """Managed Kaggle Dataset containing dependencies, not application code."""

    name: Literal["runtime"] = "runtime"
    kind: Literal["dataset"] = "dataset"

    def __init__(
        self,
        *,
        dataset_slug: str,
        runner_source: Path,
        client: KaggleResourceClient,
        builder: RuntimeCacheBuilder,
    ) -> None:
        self.unversioned_source = dataset_slug
        self._client = client
        self._builder = builder
        self._project_sha256 = file_sha256(runner_source / "pyproject.toml")
        self._lock_sha256 = file_sha256(runner_source / "uv.lock")
        self.identity_sha256 = canonical_json_sha256(self._identity_payload())

    def _identity_payload(self) -> dict[str, str]:
        return {
            "lock_sha256": self._lock_sha256,
            "platform": RUNTIME_PLATFORM,
            "project_sha256": self._project_sha256,
            "python": RUNTIME_PYTHON,
            "python_abi": RUNTIME_PYTHON_ABI,
            "resource_name": self.name,
            "uv_version": RUNTIME_UV_VERSION,
        }

    def remote_identity(self) -> str | None:
        with TemporaryDirectory(prefix="kaggle-ocr-runtime-manifest-") as temporary_directory:
            try:
                path = self._client.download_dataset_file(
                    self.unversioned_source,
                    RUNTIME_MANIFEST_NAME,
                    Path(temporary_directory),
                )
            except KaggleResourceNotFoundError:
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                return RuntimeResourceManifest.model_validate(payload).identity_sha256
            except (OSError, ValueError) as error:
                raise KaggleResourceDriftError(
                    f"Kaggle runtime Dataset {self.unversioned_source!r} has an invalid manifest"
                ) from error

    def publish(self) -> None:
        with TemporaryDirectory(prefix="kaggle-ocr-runtime-") as temporary_directory:
            source = Path(temporary_directory) / "dataset"
            source.mkdir()
            archive = source / RUNTIME_CACHE_ARCHIVE_NAME
            self._builder.build(archive)
            if not archive.is_file():
                raise KaggleResourceDriftError(
                    "Kaggle OCR runtime builder did not materialize its cache archive"
                )
            manifest = RuntimeResourceManifest(
                schema_version="1.0.0",
                resource_name="runtime",
                project_sha256=self._project_sha256,
                lock_sha256=self._lock_sha256,
                python=RUNTIME_PYTHON,
                python_abi=RUNTIME_PYTHON_ABI,
                platform=RUNTIME_PLATFORM,
                uv_version=RUNTIME_UV_VERSION,
                cache_archive=RUNTIME_CACHE_ARCHIVE_NAME,
                cache_archive_sha256=file_sha256(archive),
                identity_sha256=self.identity_sha256,
            )
            (source / RUNTIME_MANIFEST_NAME).write_text(
                manifest.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
            self._client.upload_dataset(
                self.unversioned_source,
                source,
                version_notes=f"runtime dependencies {self.identity_sha256}",
            )

    def current_version(self) -> int:
        return self._client.dataset_version(self.unversioned_source)

    def versioned_source(self, version: int) -> str:
        return f"{self.unversioned_source}/versions/{version}"
