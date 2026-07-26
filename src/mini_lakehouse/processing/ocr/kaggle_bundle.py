"""Deterministic source bundle published as the Kaggle OCR runner Dataset."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mini_lakehouse.processing.ocr.core.files import file_sha256
from mini_lakehouse.processing.ocr.core.identity import canonical_json_sha256

RUNNER_MANIFEST_NAME = "resource_manifest.json"
MODEL_MANIFEST_NAME = "mini_lakehouse_resource.json"
RUNNER_FILES = (
    "bootstrap.py",
    "runtime.py",
    "pyproject.toml",
    "uv.lock",
)
PORTABLE_PACKAGE = PurePosixPath("mini_lakehouse/processing/ocr/core")
PORTABLE_FILES = ("__init__.py", "files.py", "identity.py", "paths.py", "protocol.py")


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
            or "\\" in name
            or name in {".", ".."}
            or PurePosixPath(name).is_absolute()
            or name != PurePosixPath(name).as_posix()
            or ".." in PurePosixPath(name).parts
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
        portable_source: Path | None = None,
    ) -> KaggleRunnerBundle:
        portable = portable_source or Path(__file__).resolve().parent / "core"
        files = tuple(
            [(name, runner_source / name) for name in RUNNER_FILES]
            + [((PORTABLE_PACKAGE / name).as_posix(), portable / name) for name in PORTABLE_FILES]
        )
        missing = [str(path) for _, path in files if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"Kaggle OCR runner files are missing: {', '.join(missing)}")
        checksums = {name: file_sha256(path) for name, path in files}
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
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        (destination / RUNNER_MANIFEST_NAME).write_text(
            self.manifest.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
