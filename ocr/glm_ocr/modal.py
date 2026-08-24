"""Deploy GLM-OCR on Modal with pinned models baked into the image."""

import json
import shlex
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import modal
from document_ocr.config import GlmOcrConfig, load_arxiv_config
from document_ocr.protocol import OCR_RESULT_FILES

PROCESSOR = load_arxiv_config().pipeline("glm_ocr")
if not isinstance(PROCESSOR, GlmOcrConfig):
    raise RuntimeError("The glm_ocr pipeline must use GLM-OCR")
MODAL = PROCESSOR.modal
GLM_OCR_ROOT = Path("/root/ocr/glm_ocr")
MODEL_ROOT = Path("/models")
OUTPUT_ROOT = Path("/outputs")
_engine: Any | None = None

MODEL_DOWNLOAD_SCRIPT = ";".join(
    (
        "from huggingface_hub import snapshot_download",
        (
            "snapshot_download("
            f"repo_id={PROCESSOR.model.repository!r}, "
            f"revision={PROCESSOR.model.revision!r}, "
            f"local_dir={str(MODEL_ROOT / 'model')!r})"
        ),
        (
            "snapshot_download("
            f"repo_id={PROCESSOR.layout_model.repository!r}, "
            f"revision={PROCESSOR.layout_model.revision!r}, "
            f"local_dir={str(MODEL_ROOT / 'layout_model')!r})"
        ),
    )
)
MODEL_DOWNLOAD_COMMAND = shlex.join(("python", "-c", MODEL_DOWNLOAD_SCRIPT))


image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0")
    .uv_sync(
        "ocr/glm_ocr",
        extras=["modal"],
        frozen=True,
        uv_version="0.11.30",
    )
    .run_commands(MODEL_DOWNLOAD_COMMAND)
    .env({"PYTHONPATH": str(GLM_OCR_ROOT)})
    .add_local_dir("ocr/glm_ocr/worker", str(GLM_OCR_ROOT / "worker"))
    .add_local_dir("ocr/src/document_ocr", str(GLM_OCR_ROOT / "document_ocr"))
    .add_local_dir("ocr/config", "/root/ocr/config")
)
output_volume = modal.Volume.from_name(MODAL.output_volume, create_if_missing=True)
app = modal.App(MODAL.app_name)


def _committed_output(job: Any, target: Path) -> str | None:
    from document_ocr.output import InvalidOcrArchiveError, validate_ocr_output

    try:
        validate_ocr_output(target, expected_run_id=job.run_id)
    except InvalidOcrArchiveError:
        return None
    return target.relative_to(OUTPUT_ROOT).as_posix()


@app.function(
    image=image,
    gpu=MODAL.gpu,
    volumes={str(OUTPUT_ROOT): output_volume},
    timeout=MODAL.timeout_seconds,
    max_containers=1,
    scaledown_window=MODAL.scaledown_window_seconds,
)
def run_ocr(job_json: str) -> str:
    """Execute one idempotent document run and commit its manifest last."""
    global _engine
    from document_ocr.protocol import GlmOcrJob
    from worker.engine import InferenceEngine
    from worker.job import run

    job = GlmOcrJob.model_validate_json(job_json)
    target = OUTPUT_ROOT / "runs" / job.run_id
    committed = _committed_output(job, target)
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
        for filename in OCR_RESULT_FILES:
            shutil.copy2(temporary / filename, target / filename)
        output_volume.commit()
    committed = _committed_output(job, target)
    if committed is None:
        raise RuntimeError("Modal OCR output failed its post-commit validation")
    return committed
