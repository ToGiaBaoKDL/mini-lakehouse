"""Kaggle job preparation, submission, reconciliation, and output download."""

import json
from collections.abc import Iterator
from pathlib import Path

from mini_lakehouse.config.settings import KaggleSettings
from mini_lakehouse.contracts.processors import ProcessorContract
from mini_lakehouse.processing.ocr.core.protocol import OcrJob
from mini_lakehouse.processing.ocr.kaggle_bundle import KaggleRunnerBundle
from mini_lakehouse.processing.ocr.kaggle_gateway import KaggleGateway
from mini_lakehouse.processing.ocr.kaggle_resources import KaggleOcrResourceManager
from mini_lakehouse.processing.ocr.kaggle_types import (
    KaggleGpuQuota,
    KaggleResourceName,
    KaggleResourceResult,
    KaggleRunStatus,
    ModelSnapshotClient,
    parse_provider_run_id,
)


def render_launcher(
    *,
    job: OcrJob,
    runner_dataset_source: str,
    model_source: str,
    layout_model_source: str,
) -> str:
    """Render the only code file sent with a Kaggle kernel version."""
    return (
        f"# mini-lakehouse-batch-id: {job.batch_id}\n"
        "from pathlib import Path\n"
        "import sys\n\n"
        "import kagglehub\n\n"
        f"SOURCE = Path(kagglehub.dataset_download({runner_dataset_source!r}))\n"
        "sys.path.insert(0, str(SOURCE))\n"
        "from bootstrap import main\n\n"
        "main(\n"
        f"    job_json={job.model_dump_json()!r},\n"
        "    source=SOURCE,\n"
        f"    expected_bundle_sha256={job.runner_bundle_sha256!r},\n"
        f"    model_source={model_source!r},\n"
        f"    layout_model_source={layout_model_source!r},\n"
        ")\n"
    )


class KaggleProvider:
    def __init__(
        self,
        settings: KaggleSettings,
        processor: ProcessorContract,
        *,
        gateway: KaggleGateway | None = None,
        bundle: KaggleRunnerBundle | None = None,
        snapshots: ModelSnapshotClient | None = None,
    ) -> None:
        if not settings.configured:
            raise ValueError(
                "Kaggle OCR requires LAKEHOUSE_KAGGLE__USERNAME and LAKEHOUSE_KAGGLE__API_TOKEN"
            )
        self._settings = settings
        self._processor = processor
        self._gateway = gateway or KaggleGateway(settings)
        self._resources = KaggleOcrResourceManager(
            settings,
            processor,
            self._gateway,
            bundle=bundle,
            snapshots=snapshots,
        )
        self.kernel_slug = settings.kernel_slug(processor.runner.kernel_name)
        self.runner_bundle_sha256 = self._resources.runner_bundle_sha256

    def reconcile_resource(self, name: KaggleResourceName) -> KaggleResourceResult:
        return self._resources.reconcile(name)

    def prepare_submission(self, destination: Path, job: OcrJob) -> None:
        if job.runner_bundle_sha256 != self.runner_bundle_sha256:
            raise ValueError("OCR job and local Kaggle runner bundle identities differ")
        resources = self._resources.resolve_all()
        destination.mkdir(parents=True, exist_ok=False)
        (destination / "launcher.py").write_text(
            render_launcher(
                job=job,
                runner_dataset_source=resources.runner.source,
                model_source=resources.model.source,
                layout_model_source=resources.layout_model.source,
            ),
            encoding="utf-8",
        )
        metadata = {
            "id": self.kernel_slug,
            "title": "Mini Lakehouse ArXiv GLM OCR",
            "code_file": "launcher.py",
            "language": "python",
            "kernel_type": "script",
            "is_private": True,
            "enable_gpu": True,
            "enable_internet": True,
            "docker_image_pinning_type": "original",
            "dataset_sources": [self._resources.runner_dataset_slug],
            "competition_sources": [],
            "kernel_sources": [],
            "model_sources": resources.model_sources,
        }
        (destination / "kernel-metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def submit(self, submission_directory: Path) -> str:
        version = self._gateway.push_kernel(
            submission_directory,
            timeout_seconds=self._processor.runner.timeout_seconds,
            accelerator=self._processor.runner.accelerator,
        )
        return f"{self.kernel_slug}/{version}"

    def latest_run(self, batch_id: str) -> KaggleRunStatus | None:
        current = self._gateway.current_kernel_run(self.kernel_slug)
        if current is None:
            return None
        if f"# mini-lakehouse-batch-id: {batch_id}\n" not in current.source:
            return None
        return current.status

    def status(self, provider_run_id: str) -> KaggleRunStatus:
        self._validate_run_id(provider_run_id)
        return self._gateway.kernel_status(provider_run_id)

    def logs(self, provider_run_id: str) -> str:
        self._validate_run_id(provider_run_id)
        return self._gateway.kernel_logs(provider_run_id)

    def stream_logs(self, provider_run_id: str) -> Iterator[str]:
        self._validate_run_id(provider_run_id)
        yield from self._gateway.stream_kernel_logs(provider_run_id)

    def gpu_quota(self) -> KaggleGpuQuota:
        return self._gateway.gpu_quota()

    def download_output(self, provider_run_id: str, destination: Path) -> None:
        self._validate_run_id(provider_run_id)
        destination.mkdir(parents=True, exist_ok=False)
        for filename in ("result_manifest.json", "result.tar.zst"):
            self._gateway.download_notebook_file(
                provider_run_id,
                filename,
                destination,
            )

    def _validate_run_id(self, provider_run_id: str) -> None:
        owner, kernel, _ = parse_provider_run_id(provider_run_id)
        if f"{owner}/{kernel}" != self.kernel_slug:
            raise ValueError("Kaggle provider run does not belong to the configured kernel")
