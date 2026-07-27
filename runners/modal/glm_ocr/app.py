"""Deployed Modal compute adapter for the canonical GLM-OCR runner."""

import importlib
import json
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

import modal

from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.processing.ocr.runner_bundle import (
    MODEL_MANIFEST_NAME,
    ModelResourceManifest,
    OcrRunnerBundle,
)

PROCESSOR = load_contracts().processor("arxiv_glm_ocr")
RUNNER = PROCESSOR.runner.modal
RUNNER_BUNDLE_SHA256 = OcrRunnerBundle.load().sha256
RUNNER_ROOT = Path("/root/runners/glm_ocr")
MODEL_ROOT = Path("/models")
OUTPUT_ROOT = Path("/outputs")
_engine: Any | None = None

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0")
    .uv_sync(
        "runners/glm_ocr",
        extras=["modal"],
        frozen=True,
        uv_version="0.11.30",
    )
    .add_local_dir(
        "runners/glm_ocr",
        str(RUNNER_ROOT),
        ignore=["__pycache__"],
    )
    .add_local_dir("contracts", "/root/contracts")
    .add_local_python_source("mini_lakehouse")
)
model_volume = modal.Volume.from_name(RUNNER.model_volume, create_if_missing=True)
output_volume = modal.Volume.from_name(RUNNER.output_volume, create_if_missing=True)
app = modal.App(RUNNER.app_name)


def _resource_manifest(
    name: Literal["model", "layout_model"],
    specification: dict[str, str],
) -> ModelResourceManifest:
    from mini_lakehouse.processing.ocr.core.identity import canonical_json_sha256

    identity = {
        "repository": specification["repository"],
        "resource_name": name,
        "revision": specification["revision"],
    }
    return ModelResourceManifest(
        schema_version="1.0.0",
        resource_name=name,
        repository=specification["repository"],
        revision=specification["revision"],
        identity_sha256=canonical_json_sha256(identity),
    )


def _current_manifest(path: Path) -> ModelResourceManifest | None:
    try:
        return ModelResourceManifest.model_validate_json(
            (path / MODEL_MANIFEST_NAME).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None


@app.function(
    image=image,
    volumes={str(MODEL_ROOT): model_volume},
    max_containers=1,
    timeout=RUNNER.timeout_seconds,
)
def provision_models(specification_json: str) -> dict[str, dict[str, str]]:
    """Idempotently materialize pinned model snapshots, committing manifests last."""
    from huggingface_hub import snapshot_download

    specification = json.loads(specification_json)
    if not isinstance(specification, dict) or set(specification) != {
        "model",
        "layout_model",
    }:
        raise ValueError("Modal model specification must contain model and layout_model")
    results: dict[str, dict[str, str]] = {}
    for name in ("model", "layout_model"):
        raw = specification[name]
        if not isinstance(raw, dict):
            raise ValueError(f"Modal {name} specification must be an object")
        expected = _resource_manifest(name, raw)
        target = MODEL_ROOT / name / expected.revision
        current = _current_manifest(target)
        if current == expected:
            results[name] = {
                "action": "unchanged",
                "identity_sha256": expected.identity_sha256,
                "path": str(target),
            }
            continue
        action = "updated" if target.exists() else "created"
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        snapshot_download(
            repo_id=expected.repository,
            revision=expected.revision,
            local_dir=target,
        )
        (target / MODEL_MANIFEST_NAME).write_text(
            expected.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        model_volume.commit()
        results[name] = {
            "action": action,
            "identity_sha256": expected.identity_sha256,
            "path": str(target),
        }
    return results


def _committed_result(job: Any, target: Path) -> dict[str, str] | None:
    from mini_lakehouse.processing.ocr.core.files import file_sha256
    from mini_lakehouse.processing.ocr.core.protocol import OcrBatchManifest

    try:
        manifest = OcrBatchManifest.model_validate_json(
            (target / "result_manifest.json").read_bytes()
        )
        archive = target / "result.tar.zst"
        if (
            manifest.batch_id != job.batch_id
            or manifest.archive_size_bytes != archive.stat().st_size
            or manifest.archive_sha256 != file_sha256(archive)
        ):
            return None
    except (OSError, ValueError):
        return None
    return {
        "batch_id": job.batch_id,
        "output_prefix": target.relative_to(OUTPUT_ROOT).as_posix(),
        "state": "complete",
    }


@app.function(
    image=image,
    gpu=RUNNER.gpu,
    volumes={
        str(MODEL_ROOT): model_volume,
        str(OUTPUT_ROOT): output_volume,
    },
    timeout=RUNNER.timeout_seconds,
    max_containers=RUNNER.max_containers,
    scaledown_window=RUNNER.scaledown_window_seconds,
)
def run_ocr(job_json: str) -> dict[str, str]:
    """Execute one idempotent batch and publish its provider output manifest last."""
    global _engine
    sys.path.insert(0, str(RUNNER_ROOT))
    from mini_lakehouse.processing.ocr.core.protocol import OcrJob

    job = OcrJob.model_validate_json(job_json)
    if job.runner_bundle_sha256 != RUNNER_BUNDLE_SHA256:
        raise ValueError("OCR job does not match the deployed Modal runner source")
    target = OUTPUT_ROOT / "runs" / job.batch_id
    committed = _committed_result(job, target)
    if committed is not None:
        print(
            json.dumps(
                {"event": "batch_reused", "batch_id": job.batch_id},
                separators=(",", ":"),
            ),
            flush=True,
        )
        return committed

    if target.exists():
        shutil.rmtree(target)
    model_path = MODEL_ROOT / "model" / job.model.revision
    layout_model_path = MODEL_ROOT / "layout_model" / job.layout_model.revision
    runtime = importlib.import_module("runtime")
    run = runtime.run
    if _engine is None:
        _engine = runtime.InferenceEngine(Path("/tmp/mini-lakehouse-inference"))
    with TemporaryDirectory(prefix="mini-lakehouse-ocr-") as temporary_directory:
        temporary = Path(temporary_directory)
        run(
            job,
            temporary,
            model_source=str(model_path),
            layout_model_source=str(layout_model_path),
            engine=_engine,
        )
        target.mkdir(parents=True)
        shutil.copy2(temporary / "result.tar.zst", target / "result.tar.zst")
        output_volume.commit()
        shutil.copy2(temporary / "result_manifest.json", target / "result_manifest.json")
        output_volume.commit()
    committed = _committed_result(job, target)
    if committed is None:
        raise RuntimeError("Modal OCR output failed its post-commit validation")
    return committed
