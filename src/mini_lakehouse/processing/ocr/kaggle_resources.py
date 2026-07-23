"""Versioned Kaggle resources used by the remote OCR runner."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mini_lakehouse.config.settings import KaggleSettings
from mini_lakehouse.contracts.processors import ProcessorContract
from mini_lakehouse.processing.ocr.identity import canonical_json_sha256

RUNNER_MANIFEST_NAME = "resource_manifest.json"
RUNNER_FILES = (
    "bootstrap.py",
    "runtime.py",
    "pyproject.toml",
    "uv.lock",
)
SHARED_FILES = ("identity.py", "protocol.py")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class RunnerResourceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0.0"]
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: dict[str, str]

    @field_validator("files")
    @classmethod
    def validate_files(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("Runner resource manifest must contain files")
        if any(
            not name
            or "/" in name
            or "\\" in name
            or name in {".", ".."}
            or len(checksum) != 64
            or any(character not in "0123456789abcdef" for character in checksum)
            for name, checksum in value.items()
        ):
            raise ValueError("Runner resource manifest contains an invalid file entry")
        return value

    @model_validator(mode="after")
    def validate_bundle_identity(self) -> RunnerResourceManifest:
        if canonical_json_sha256({"files": self.files}) != self.bundle_sha256:
            raise ValueError("Runner resource bundle identity does not match its file manifest")
        return self


@dataclass(frozen=True)
class KaggleRunnerBundle:
    """A deterministic local view of every file executed by the remote runner."""

    files: tuple[tuple[str, Path], ...]
    manifest: RunnerResourceManifest

    @classmethod
    def load(
        cls,
        *,
        runner_source: Path = Path("runners/kaggle/glm_ocr"),
        shared_source: Path | None = None,
    ) -> KaggleRunnerBundle:
        shared = shared_source or Path(__file__).resolve().parent
        files = tuple(
            [(name, runner_source / name) for name in RUNNER_FILES]
            + [(name, shared / name) for name in SHARED_FILES]
        )
        missing = [str(path) for _, path in files if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"Kaggle OCR runner files are missing: {', '.join(missing)}")
        checksums = {name: _file_sha256(path) for name, path in files}
        bundle_sha256 = canonical_json_sha256({"files": checksums})
        return cls(
            files=files,
            manifest=RunnerResourceManifest(
                schema_version="1.0.0",
                bundle_sha256=bundle_sha256,
                files=checksums,
            ),
        )

    @property
    def sha256(self) -> str:
        return self.manifest.bundle_sha256

    def write(self, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        for name, source in self.files:
            shutil.copy2(source, destination / name)
        (destination / RUNNER_MANIFEST_NAME).write_text(
            self.manifest.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )


class KaggleResourceClient(Protocol):
    def download_dataset_file(
        self,
        dataset_slug: str,
        relative_path: str,
        destination: Path,
    ) -> Path: ...

    def upload_dataset(
        self,
        dataset_slug: str,
        source: Path,
        *,
        version_notes: str,
    ) -> None: ...

    def dataset_version(self, dataset_slug: str) -> int: ...


class KaggleResourceNotFoundError(RuntimeError):
    pass


class KaggleResourceDriftError(RuntimeError):
    pass


class KaggleResourceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["created", "updated", "unchanged"]
    dataset_source: str
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class KaggleResourceProvisioner:
    """Reconcile the private runner Dataset by immutable content identity."""

    def __init__(
        self,
        settings: KaggleSettings,
        processor: ProcessorContract,
        client: KaggleResourceClient,
        *,
        bundle: KaggleRunnerBundle | None = None,
        readiness_attempts: int = 36,
        readiness_delay_seconds: float = 5,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not settings.configured:
            raise ValueError(
                "Kaggle resources require LAKEHOUSE_KAGGLE__USERNAME and "
                "LAKEHOUSE_KAGGLE__API_TOKEN"
            )
        self._settings = settings
        self._processor = processor
        self._client = client
        self._bundle = bundle or KaggleRunnerBundle.load()
        self._readiness_attempts = readiness_attempts
        self._readiness_delay_seconds = readiness_delay_seconds
        self._sleep = sleeper
        self.dataset_slug = settings.dataset_slug(processor.runner.runner_dataset_name)

    def provision(self) -> KaggleResourceResult:
        remote = self._remote_manifest()
        if remote == self._bundle.manifest:
            return self._result("unchanged")

        action: Literal["created", "updated"] = "created" if remote is None else "updated"
        with TemporaryDirectory(prefix="kaggle-ocr-runner-") as temporary_directory:
            source = Path(temporary_directory) / "dataset"
            self._bundle.write(source)
            self._client.upload_dataset(
                self.dataset_slug,
                source,
                version_notes=f"runner bundle {self._bundle.sha256}",
            )

        for attempt in range(self._readiness_attempts):
            remote = self._remote_manifest()
            if remote == self._bundle.manifest:
                return self._result(action)
            if attempt + 1 < self._readiness_attempts:
                self._sleep(self._readiness_delay_seconds)
        raise KaggleResourceDriftError(
            f"Kaggle runner Dataset {self.dataset_slug!r} did not expose "
            f"bundle {self._bundle.sha256} after upload"
        )

    def resolve(self) -> str:
        remote = self._remote_manifest()
        if remote is None:
            raise KaggleResourceNotFoundError(
                f"Kaggle runner Dataset {self.dataset_slug!r} is missing; "
                "run gov_arxiv_ocr_resources first"
            )
        if remote != self._bundle.manifest:
            raise KaggleResourceDriftError(
                f"Kaggle runner Dataset {self.dataset_slug!r} contains "
                f"{remote.bundle_sha256}, expected {self._bundle.sha256}; "
                "run gov_arxiv_ocr_resources first"
            )
        return self._versioned_source()

    def _remote_manifest(self) -> RunnerResourceManifest | None:
        with TemporaryDirectory(prefix="kaggle-ocr-manifest-") as temporary_directory:
            try:
                path = self._client.download_dataset_file(
                    self.dataset_slug,
                    RUNNER_MANIFEST_NAME,
                    Path(temporary_directory),
                )
            except KaggleResourceNotFoundError:
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                return RunnerResourceManifest.model_validate(payload)
            except (OSError, ValueError) as error:
                raise KaggleResourceDriftError(
                    f"Kaggle runner Dataset {self.dataset_slug!r} has an invalid manifest"
                ) from error

    def _result(
        self,
        action: Literal["created", "updated", "unchanged"],
    ) -> KaggleResourceResult:
        return KaggleResourceResult(
            action=action,
            dataset_source=self._versioned_source(),
            bundle_sha256=self._bundle.sha256,
        )

    def _versioned_source(self) -> str:
        version = self._client.dataset_version(self.dataset_slug)
        if version < 1:
            raise KaggleResourceDriftError(
                f"Kaggle runner Dataset {self.dataset_slug!r} has no published version"
            )
        return f"{self.dataset_slug}/{version}"
