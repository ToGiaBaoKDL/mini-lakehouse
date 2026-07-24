"""Desired-state reconciliation for versioned Kaggle OCR resources."""

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
from mini_lakehouse.contracts.processors import (
    KaggleModelResourceContract,
    ProcessorContract,
    ProcessorModelContract,
)
from mini_lakehouse.processing.ocr.identity import canonical_json_sha256

RUNNER_MANIFEST_NAME = "resource_manifest.json"
MODEL_MANIFEST_NAME = "mini_lakehouse_resource.json"
RUNNER_FILES = (
    "bootstrap.py",
    "runtime.py",
    "pyproject.toml",
    "uv.lock",
)
SHARED_FILES = ("identity.py", "protocol.py")

KaggleResourceName = Literal["runner", "model", "layout_model"]
KaggleResourceKind = Literal["dataset", "model"]
KaggleResourceAction = Literal["created", "updated", "unchanged"]


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


class ModelResourceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0.0"]
    resource_name: Literal["model", "layout_model"]
    repository: str
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_identity(self) -> ModelResourceManifest:
        identity = {
            "repository": self.repository,
            "resource_name": self.resource_name,
            "revision": self.revision,
        }
        if canonical_json_sha256(identity) != self.identity_sha256:
            raise ValueError("Model resource identity does not match its source revision")
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

    def download_model_file(
        self,
        model_slug: str,
        relative_path: str,
        destination: Path,
    ) -> Path: ...

    def upload_model(
        self,
        model_slug: str,
        source: Path,
        *,
        license_name: str,
        version_notes: str,
    ) -> None: ...

    def model_version(self, model_slug: str) -> int: ...


class ModelSnapshotClient(Protocol):
    def download(
        self,
        repository: str,
        revision: str,
        destination: Path,
    ) -> None: ...


class HuggingFaceSnapshotClient:
    """Materialize one immutable Hugging Face revision for publication."""

    def download(
        self,
        repository: str,
        revision: str,
        destination: Path,
    ) -> None:
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=repository,
            revision=revision,
            local_dir=destination,
        )


class KaggleResourceNotFoundError(RuntimeError):
    pass


class KaggleResourceDriftError(RuntimeError):
    pass


class KaggleResourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: KaggleResourceName
    kind: KaggleResourceKind
    source: str
    identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class KaggleResourceResult(KaggleResourceReference):
    action: KaggleResourceAction


class KaggleOcrResourceReferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    runner: KaggleResourceReference
    model: KaggleResourceReference
    layout_model: KaggleResourceReference

    @model_validator(mode="after")
    def validate_roles(self) -> KaggleOcrResourceReferences:
        if (
            self.runner.name != "runner"
            or self.runner.kind != "dataset"
            or self.model.name != "model"
            or self.model.kind != "model"
            or self.layout_model.name != "layout_model"
            or self.layout_model.kind != "model"
        ):
            raise ValueError("Kaggle OCR resource references do not match their roles")
        return self

    @property
    def model_sources(self) -> list[str]:
        return [self.model.source, self.layout_model.source]


class ManagedKaggleResource(Protocol):
    @property
    def name(self) -> KaggleResourceName: ...

    @property
    def kind(self) -> KaggleResourceKind: ...

    @property
    def identity_sha256(self) -> str: ...

    @property
    def unversioned_source(self) -> str: ...

    def remote_identity(self) -> str | None: ...

    def publish(self) -> None: ...

    def current_version(self) -> int: ...

    def versioned_source(self, version: int) -> str: ...


class KaggleResourceReconciler:
    """Apply one versioned resource's desired state and verify it by read-back."""

    def __init__(
        self,
        *,
        readiness_attempts: int = 36,
        readiness_delay_seconds: float = 5,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if readiness_attempts < 1:
            raise ValueError("readiness_attempts must be at least one")
        if readiness_delay_seconds < 0:
            raise ValueError("readiness_delay_seconds cannot be negative")
        self._readiness_attempts = readiness_attempts
        self._readiness_delay_seconds = readiness_delay_seconds
        self._sleep = sleeper

    def reconcile(self, resource: ManagedKaggleResource) -> KaggleResourceResult:
        current_version = self._current_version(resource)
        remote_identity = resource.remote_identity() if current_version is not None else None
        if current_version is not None and remote_identity == resource.identity_sha256:
            return self._result(resource, "unchanged", current_version)

        resource.publish()
        for attempt in range(self._readiness_attempts):
            if resource.remote_identity() == resource.identity_sha256:
                version = self._required_version(resource)
                action: KaggleResourceAction = (
                    "updated" if current_version is not None else "created"
                )
                return self._result(resource, action, version)
            if attempt + 1 < self._readiness_attempts:
                self._sleep(self._readiness_delay_seconds)
        raise KaggleResourceDriftError(
            f"Kaggle {resource.kind} {resource.unversioned_source!r} did not expose "
            f"identity {resource.identity_sha256} after publication"
        )

    def resolve(self, resource: ManagedKaggleResource) -> KaggleResourceReference:
        version = self._required_version(resource)
        remote_identity = resource.remote_identity()
        if remote_identity is None:
            raise KaggleResourceNotFoundError(
                f"Kaggle {resource.kind} {resource.unversioned_source!r} has no managed "
                "manifest; run gov_arxiv_ocr_resources first"
            )
        if remote_identity != resource.identity_sha256:
            raise KaggleResourceDriftError(
                f"Kaggle {resource.kind} {resource.unversioned_source!r} contains "
                f"identity {remote_identity}, expected {resource.identity_sha256}; "
                "run gov_arxiv_ocr_resources first"
            )
        return KaggleResourceReference(
            name=resource.name,
            kind=resource.kind,
            source=resource.versioned_source(version),
            identity_sha256=resource.identity_sha256,
        )

    def _result(
        self,
        resource: ManagedKaggleResource,
        action: KaggleResourceAction,
        version: int,
    ) -> KaggleResourceResult:
        return KaggleResourceResult(
            name=resource.name,
            kind=resource.kind,
            source=resource.versioned_source(version),
            identity_sha256=resource.identity_sha256,
            action=action,
        )

    @staticmethod
    def _current_version(resource: ManagedKaggleResource) -> int | None:
        try:
            version = resource.current_version()
        except KaggleResourceNotFoundError:
            return None
        if version < 1:
            raise KaggleResourceDriftError(
                f"Kaggle {resource.kind} {resource.unversioned_source!r} has no published version"
            )
        return version

    def _required_version(self, resource: ManagedKaggleResource) -> int:
        version = self._current_version(resource)
        if version is None:
            raise KaggleResourceNotFoundError(
                f"Kaggle {resource.kind} {resource.unversioned_source!r} is missing; "
                "run gov_arxiv_ocr_resources first"
            )
        return version


class KaggleRunnerDatasetResource:
    name: Literal["runner"] = "runner"
    kind: Literal["dataset"] = "dataset"

    def __init__(
        self,
        dataset_slug: str,
        bundle: KaggleRunnerBundle,
        client: KaggleResourceClient,
    ) -> None:
        self.unversioned_source = dataset_slug
        self.identity_sha256 = bundle.sha256
        self._bundle = bundle
        self._client = client

    def remote_identity(self) -> str | None:
        with TemporaryDirectory(prefix="kaggle-ocr-runner-manifest-") as temporary_directory:
            try:
                path = self._client.download_dataset_file(
                    self.unversioned_source,
                    RUNNER_MANIFEST_NAME,
                    Path(temporary_directory),
                )
            except KaggleResourceNotFoundError:
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                return RunnerResourceManifest.model_validate(payload).bundle_sha256
            except (OSError, ValueError) as error:
                raise KaggleResourceDriftError(
                    f"Kaggle runner Dataset {self.unversioned_source!r} has an invalid manifest"
                ) from error

    def publish(self) -> None:
        with TemporaryDirectory(prefix="kaggle-ocr-runner-") as temporary_directory:
            source = Path(temporary_directory) / "dataset"
            self._bundle.write(source)
            self._client.upload_dataset(
                self.unversioned_source,
                source,
                version_notes=f"runner bundle {self.identity_sha256}",
            )

    def current_version(self) -> int:
        return self._client.dataset_version(self.unversioned_source)

    def versioned_source(self, version: int) -> str:
        return f"{self.unversioned_source}/{version}"


class KaggleModelResource:
    name: Literal["model", "layout_model"]
    kind: Literal["model"] = "model"

    def __init__(
        self,
        *,
        name: Literal["model", "layout_model"],
        model: ProcessorModelContract,
        target: KaggleModelResourceContract,
        settings: KaggleSettings,
        client: KaggleResourceClient,
        snapshots: ModelSnapshotClient,
    ) -> None:
        self.name = name
        self.unversioned_source = settings.model_slug(
            target.name,
            framework=target.framework,
            variation=target.variation,
        )
        self._model = model
        self._target = target
        self._client = client
        self._snapshots = snapshots
        identity = {
            "repository": model.repository,
            "resource_name": name,
            "revision": model.revision,
        }
        self.identity_sha256 = canonical_json_sha256(identity)
        self._manifest = ModelResourceManifest(
            schema_version="1.0.0",
            resource_name=name,
            repository=model.repository,
            revision=model.revision,
            identity_sha256=self.identity_sha256,
        )

    def remote_identity(self) -> str | None:
        with TemporaryDirectory(prefix=f"kaggle-ocr-{self.name}-manifest-") as temporary_directory:
            try:
                path = self._client.download_model_file(
                    self.unversioned_source,
                    MODEL_MANIFEST_NAME,
                    Path(temporary_directory),
                )
            except KaggleResourceNotFoundError:
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                return ModelResourceManifest.model_validate(payload).identity_sha256
            except (OSError, ValueError) as error:
                raise KaggleResourceDriftError(
                    f"Kaggle model {self.unversioned_source!r} has an invalid manifest"
                ) from error

    def publish(self) -> None:
        with TemporaryDirectory(prefix=f"kaggle-ocr-{self.name}-") as temporary_directory:
            source = Path(temporary_directory) / "model"
            self._snapshots.download(
                self._model.repository,
                self._model.revision,
                source,
            )
            if not source.is_dir():
                raise KaggleResourceDriftError(
                    f"Model snapshot {self._model.repository!r} was not materialized"
                )
            (source / MODEL_MANIFEST_NAME).write_text(
                self._manifest.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
            self._client.upload_model(
                self.unversioned_source,
                source,
                license_name=self._target.license_name,
                version_notes=(
                    f"{self._model.repository}@{self._model.revision} "
                    f"identity {self.identity_sha256}"
                ),
            )

    def current_version(self) -> int:
        return self._client.model_version(self.unversioned_source)

    def versioned_source(self, version: int) -> str:
        return f"{self.unversioned_source}/{version}"


class KaggleOcrResourceManager:
    """Own the complete desired state of resources needed by one Kaggle OCR runner."""

    def __init__(
        self,
        settings: KaggleSettings,
        processor: ProcessorContract,
        client: KaggleResourceClient,
        *,
        bundle: KaggleRunnerBundle | None = None,
        snapshots: ModelSnapshotClient | None = None,
        readiness_attempts: int = 36,
        readiness_delay_seconds: float = 5,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not settings.configured:
            raise ValueError(
                "Kaggle resources require LAKEHOUSE_KAGGLE__USERNAME and "
                "LAKEHOUSE_KAGGLE__API_TOKEN"
            )
        runner_bundle = bundle or KaggleRunnerBundle.load()
        model_targets = processor.runner.model_resources
        self._resources: dict[KaggleResourceName, ManagedKaggleResource] = {
            "runner": KaggleRunnerDatasetResource(
                settings.dataset_slug(processor.runner.runner_dataset_name),
                runner_bundle,
                client,
            ),
            "model": KaggleModelResource(
                name="model",
                model=processor.model,
                target=model_targets.model,
                settings=settings,
                client=client,
                snapshots=snapshots or HuggingFaceSnapshotClient(),
            ),
            "layout_model": KaggleModelResource(
                name="layout_model",
                model=processor.layout_model,
                target=model_targets.layout_model,
                settings=settings,
                client=client,
                snapshots=snapshots or HuggingFaceSnapshotClient(),
            ),
        }
        self._reconciler = KaggleResourceReconciler(
            readiness_attempts=readiness_attempts,
            readiness_delay_seconds=readiness_delay_seconds,
            sleeper=sleeper,
        )
        self.runner_bundle_sha256 = runner_bundle.sha256

    def reconcile(self, name: KaggleResourceName) -> KaggleResourceResult:
        return self._reconciler.reconcile(self._resources[name])

    def resolve(self, name: KaggleResourceName) -> KaggleResourceReference:
        return self._reconciler.resolve(self._resources[name])

    def resolve_all(self) -> KaggleOcrResourceReferences:
        return KaggleOcrResourceReferences(
            runner=self.resolve("runner"),
            model=self.resolve("model"),
            layout_model=self.resolve("layout_model"),
        )
