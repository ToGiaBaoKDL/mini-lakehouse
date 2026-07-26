"""Bootstrap the locked OCR environment from the versioned runner Dataset."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

UV_VERSION = "0.11.30"
MANIFEST_NAME = "resource_manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_bundle(source: Path, expected_bundle_sha256: str) -> None:
    payload = json.loads((source / MANIFEST_NAME).read_text(encoding="utf-8"))
    if payload.get("bundle_sha256") != expected_bundle_sha256:
        raise RuntimeError("Mounted Kaggle runner Dataset has an unexpected content identity")
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise RuntimeError("Mounted Kaggle runner Dataset has an invalid file manifest")
    manifest_identity = hashlib.sha256(
        json.dumps(
            {"files": files},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if manifest_identity != expected_bundle_sha256:
        raise RuntimeError("Mounted Kaggle runner file manifest has an invalid content identity")
    for name, expected_sha256 in files.items():
        relative = PurePosixPath(name) if isinstance(name, str) else PurePosixPath(".")
        if (
            not isinstance(name, str)
            or "\\" in name
            or name in {".", ".."}
            or relative.is_absolute()
            or name != relative.as_posix()
            or ".." in relative.parts
            or not isinstance(expected_sha256, str)
        ):
            raise RuntimeError(f"Mounted Kaggle runner file failed validation: {name!r}")
        path = source.joinpath(*relative.parts)
        if not path.is_file() or _sha256(path) != expected_sha256:
            raise RuntimeError(f"Mounted Kaggle runner file failed validation: {name!r}")


def _uv_version(uv: str) -> str | None:
    completed = subprocess.run(
        [uv, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    parts = completed.stdout.strip().split()
    return parts[1] if completed.returncode == 0 and len(parts) >= 2 and parts[0] == "uv" else None


def _ensure_uv(version: str) -> str:
    uv = shutil.which("uv")
    if uv is None or _uv_version(uv) != version:
        _run(
            "uv_install",
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--upgrade",
                f"uv=={version}",
            ],
        )
        uv = shutil.which("uv")
    if uv is None or _uv_version(uv) != version:
        raise RuntimeError(f"uv {version} installation completed but is unavailable")
    return uv


def _run(stage: str, command: list[str], *, environment: dict[str, str] | None = None) -> None:
    started_at = time.perf_counter()
    print(json.dumps({"event": f"{stage}_started"}, separators=(",", ":")), flush=True)
    subprocess.run(command, check=True, env=environment)
    print(
        json.dumps(
            {
                "event": f"{stage}_completed",
                "elapsed_seconds": round(time.perf_counter() - started_at, 3),
            },
            separators=(",", ":"),
        ),
        flush=True,
    )


def main(
    *,
    job_json: str,
    source: Path,
    expected_bundle_sha256: str,
    model_source: str,
    layout_model_source: str,
) -> None:
    _validate_bundle(source, expected_bundle_sha256)
    working = Path("/kaggle/working")
    working.mkdir(parents=True, exist_ok=True)
    project = Path(tempfile.mkdtemp(prefix="mini-lakehouse-glm-ocr-"))
    try:
        shutil.copytree(source, project, dirs_exist_ok=True)
        (project / "job.json").write_text(job_json, encoding="utf-8")
        environment = os.environ.copy()
        environment["UV_PROJECT_ENVIRONMENT"] = str(project / ".venv")
        environment["UV_CACHE_DIR"] = "/tmp/mini-lakehouse-uv-cache"
        environment["UV_LINK_MODE"] = "hardlink"
        uv = _ensure_uv(UV_VERSION)
        _run(
            "dependency_sync",
            [
                uv,
                "sync",
                "--project",
                str(project),
                "--frozen",
                "--no-dev",
                "--no-build",
                "--link-mode",
                "hardlink",
            ],
            environment=environment,
        )
        _run(
            "ocr_runtime",
            [
                uv,
                "run",
                "--project",
                str(project),
                "--frozen",
                "--no-sync",
                "python",
                str(project / "runtime.py"),
                "--output-directory",
                str(working),
                "--model-source",
                model_source,
                "--layout-model-source",
                layout_model_source,
            ],
            environment=environment,
        )
    finally:
        shutil.rmtree(project, ignore_errors=True)
