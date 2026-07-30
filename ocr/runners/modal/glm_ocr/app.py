"""Deploy GLM-OCR on Modal with pinned models baked into the image."""

import json
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import modal
from document_ocr.config import load_ocr_config

PROCESSOR = load_ocr_config("arxiv_glm_ocr")
RUNNER = PROCESSOR.runner.modal
RUNNER_ROOT = Path("/root/ocr/runners/glm_ocr")
MODEL_ROOT = Path("/models")
OUTPUT_ROOT = Path("/outputs")
_engine: Any | None = None


def download_models(
    model_repository: str,
    model_revision: str,
    layout_repository: str,
    layout_revision: str,
) -> None:
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=model_repository,
        revision=model_revision,
        local_dir=MODEL_ROOT / "model",
    )
    snapshot_download(
        repo_id=layout_repository,
        revision=layout_revision,
        local_dir=MODEL_ROOT / "layout_model",
    )


image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0")
    .uv_sync(
        "ocr/runners/glm_ocr",
        extras=["modal"],
        frozen=True,
        uv_version="0.11.30",
    )
    .run_function(
        download_models,
        args=(
            PROCESSOR.model.repository,
            PROCESSOR.model.revision,
            PROCESSOR.layout_model.repository,
            PROCESSOR.layout_model.revision,
        ),
    )
    .add_local_file("ocr/runners/glm_ocr/runtime.py", str(RUNNER_ROOT / "runtime.py"))
    .add_local_dir("ocr/runners/glm_ocr/runner", str(RUNNER_ROOT / "runner"))
    .add_local_dir("ocr/src/document_ocr", str(RUNNER_ROOT / "document_ocr"))
)
output_volume = modal.Volume.from_name(RUNNER.output_volume, create_if_missing=True)
app = modal.App(RUNNER.app_name)


def _committed_result(job: Any, target: Path) -> dict[str, str] | None:
    from document_ocr.archive import InvalidOcrArchiveError, validate_ocr_output

    try:
        validate_ocr_output(target, expected_run_id=job.run_id)
    except InvalidOcrArchiveError:
        return None
    return {
        "run_id": job.run_id,
        "output_prefix": target.relative_to(OUTPUT_ROOT).as_posix(),
        "state": "complete",
    }


@app.function(
    image=image,
    gpu=RUNNER.gpu,
    volumes={str(OUTPUT_ROOT): output_volume},
    timeout=RUNNER.timeout_seconds,
    max_containers=1,
    scaledown_window=RUNNER.scaledown_window_seconds,
)
def run_ocr(job_json: str) -> dict[str, str]:
    """Execute one idempotent document run and commit its manifest last."""
    global _engine
    sys.path.insert(0, str(RUNNER_ROOT))
    from document_ocr.protocol import OcrJob
    from runner.engine import InferenceEngine
    from runner.job import run

    job = OcrJob.model_validate_json(job_json)
    target = OUTPUT_ROOT / "runs" / job.run_id
    committed = _committed_result(job, target)
    if committed is not None:
        print(
            json.dumps(
                {"event": "run_reused", "run_id": job.run_id},
                separators=(",", ":"),
            ),
            flush=True,
        )
        return committed

    if target.exists():
        shutil.rmtree(target)
    if _engine is None:
        _engine = InferenceEngine(Path("/tmp/document-ocr-inference"))
    with TemporaryDirectory(prefix="document-ocr-") as temporary_directory:
        temporary = Path(temporary_directory)
        run(
            job,
            temporary,
            model_path=MODEL_ROOT / "model",
            layout_model_path=MODEL_ROOT / "layout_model",
            engine=_engine,
        )
        target.mkdir(parents=True)
        shutil.copy2(temporary / "result.tar.zst", target / "result.tar.zst")
        shutil.copy2(temporary / "result_manifest.json", target / "result_manifest.json")
        output_volume.commit()
    committed = _committed_result(job, target)
    if committed is None:
        raise RuntimeError("Modal OCR output failed its post-commit validation")
    return committed
