"""Deploy the self-hosted GLM-OCR SDK as one lifecycle-managed Modal class."""

import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import modal
from document_ocr.config import load_config
from document_ocr.protocol import OCR_RESULT_FILES, OcrJob

CONFIG = load_config()
MODAL = CONFIG.modal
MODAL_ROOT = Path("/root/ocr-engine/modal")
MODEL_ROOT = Path("/models")
OUTPUT_ROOT = Path("/outputs")


def download_models(
    model_repository: str,
    model_revision: str,
    layout_repository: str,
    layout_revision: str,
) -> None:
    """Bake immutable Hugging Face snapshots into the Modal image."""
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
        "ocr-engine/modal",
        extras=["modal"],
        frozen=True,
        uv_version="0.11.30",
    )
    .run_function(
        download_models,
        args=(
            CONFIG.model.repository,
            CONFIG.model.revision,
            CONFIG.layout_model.repository,
            CONFIG.layout_model.revision,
        ),
    )
    .env({"PYTHONPATH": str(MODAL_ROOT)})
    .add_local_dir("ocr-engine/modal", str(MODAL_ROOT))
    .add_local_dir("ocr-engine/src/document_ocr", str(MODAL_ROOT / "document_ocr"))
    .add_local_file("ocr-engine/config.yaml", "/root/ocr-engine/config.yaml")
)
output_volume = modal.Volume.from_name(MODAL.output_volume, create_if_missing=True)
app = modal.App(MODAL.app_name)


def _committed_output(job: OcrJob, target: Path) -> str | None:
    from document_ocr.output import InvalidOcrArchiveError, validate_ocr_output

    try:
        validate_ocr_output(target, expected_job_id=job.job_id)
    except InvalidOcrArchiveError:
        return None
    return target.relative_to(OUTPUT_ROOT).as_posix()


@app.cls(
    image=image,
    gpu=MODAL.gpu,
    volumes={str(OUTPUT_ROOT): output_volume},
    timeout=MODAL.timeout_seconds,
    max_containers=1,
    scaledown_window=MODAL.scaledown_window_seconds,
)
class Ocr:
    @modal.enter()
    def start(self) -> None:
        from server import start

        self._process, self._log_file, self._parser = start(
            CONFIG,
            model_path=MODEL_ROOT / "model",
            layout_model_path=MODEL_ROOT / "layout_model",
            root=Path("/tmp/ocr-engine"),
        )

    @modal.exit()
    def stop(self) -> None:
        from server import stop

        self._parser.close()
        stop(self._process, self._log_file)

    @modal.method()
    def run(self, job_json: str) -> str:
        """Execute one idempotent document run and commit its manifest last."""
        from worker import run

        job = OcrJob.model_validate_json(job_json)
        if job.config_hash != CONFIG.configuration_hash:
            raise ValueError("OCR job configuration does not match the deployed Modal runtime")
        target = OUTPUT_ROOT / "jobs" / job.job_id
        committed = _committed_output(job, target)
        if committed is not None:
            print(f"Reusing committed Modal output for {job.document_id}", flush=True)
            return committed

        if target.exists():
            shutil.rmtree(target)
        with TemporaryDirectory(prefix="ocr-engine-") as temporary_directory:
            temporary = Path(temporary_directory)
            run(job, temporary, parser=self._parser)
            target.mkdir(parents=True)
            for filename in OCR_RESULT_FILES:
                shutil.copy2(temporary / filename, target / filename)
            output_volume.commit()
        committed = _committed_output(job, target)
        if committed is None:
            raise RuntimeError("Modal OCR output failed its post-commit validation")
        return committed
