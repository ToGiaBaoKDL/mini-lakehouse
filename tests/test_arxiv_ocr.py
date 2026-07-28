import gzip
import io
import json
import tarfile
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
import zstandard

from mini_lakehouse.config.settings import KaggleSettings, Settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.curated.arxiv.models import ActiveOcrBatch, OcrBatchDocument, OcrCandidate
from mini_lakehouse.curated.arxiv.ocr_service import ArxivOcrService
from mini_lakehouse.processing.ocr.archive import (
    InvalidOcrOutputError,
    document_manifest_sha256,
    extract_archive,
    validate_runner_output,
)
from mini_lakehouse.processing.ocr.core.identity import (
    batch_id,
    config_hash,
    file_sha256,
    processing_id,
    request_id,
)
from mini_lakehouse.processing.ocr.core.paths import runner_document_path
from mini_lakehouse.processing.ocr.core.protocol import (
    OCR_OUTPUT_SCHEMA_VERSION,
    ArtifactFile,
    OcrBatchManifest,
    OcrDocumentRequest,
    OcrDocumentResult,
    OcrElement,
    OcrInference,
    OcrJob,
    OcrLimits,
    OcrModel,
)
from mini_lakehouse.processing.ocr.core.text import build_page_markdown_bundle
from mini_lakehouse.processing.ocr.kaggle_provider import KaggleProvider, render_launcher
from mini_lakehouse.processing.ocr.provider import OcrProviderState, OcrRunStatus
from runners.kaggle.glm_ocr.deploy import build_runner

SOURCE_RECORD_SHA256 = "a" * 64


def _job(attempt_count: int = 1, *, configuration_hash: str | None = None) -> OcrJob:
    processor = load_contracts().processor("arxiv_glm_ocr")
    current_hash = configuration_hash or config_hash(processor)
    request = OcrDocumentRequest(
        request_id=request_id(
            arxiv_id="hep-th/9901001",
            source_record_sha256=SOURCE_RECORD_SHA256,
            configuration_hash=current_hash,
        ),
        arxiv_id="hep-th/9901001",
        oai_datestamp=date(2026, 7, 22),
        source_record_sha256=SOURCE_RECORD_SHA256,
        pdf_url="https://arxiv.org/pdf/hep-th/9901001",
    )
    key = batch_id((request.request_id,), attempts={request.request_id: attempt_count})
    return OcrJob(
        schema_version="1.0.0",
        batch_id=key,
        model=OcrModel.model_validate(processor.model.model_dump()),
        layout_model=OcrModel.model_validate(processor.layout_model.model_dump()),
        adapter_version=processor.adapter_version,
        output_schema_version=OCR_OUTPUT_SCHEMA_VERSION,
        config_hash=current_hash,
        limits=OcrLimits(
            max_pdf_bytes=processor.batch.max_pdf_bytes,
            max_pages_per_document=processor.batch.max_pages_per_document,
            max_output_bytes=processor.batch.max_output_bytes,
        ),
        inference=OcrInference.model_validate(processor.inference.model_dump()),
        documents=(request,),
    )


def _gzip(path: Path, content: bytes) -> None:
    with (
        path.open("xb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
    ):
        compressed.write(content)


def _archive(source: Path, destination: Path) -> None:
    with (
        destination.open("xb") as raw,
        zstandard.ZstdCompressor().stream_writer(raw) as compressed,
        tarfile.open(fileobj=compressed, mode="w|") as bundle,
    ):
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            info = tarfile.TarInfo(path.relative_to(source).as_posix())
            info.size = path.stat().st_size
            with path.open("rb") as source_file:
                bundle.addfile(info, source_file)


def _successful_output(output: Path, job: OcrJob) -> None:
    request = job.documents[0]
    content = output / "content"
    document = content.joinpath(*runner_document_path(request.arxiv_id, request.request_id).parts)
    document.mkdir(parents=True)
    element = OcrElement(
        element_id="e" * 64,
        page_number=1,
        reading_order=0,
        element_type="text",
        text_content="Paper",
        markdown_content="Paper",
    )
    pages = build_page_markdown_bundle((element,), page_count=1)
    _gzip(document / "pages.json.gz", pages.model_dump_json().encode())
    _gzip(document / "elements.jsonl.gz", f"{element.model_dump_json()}\n".encode())
    visualizations = document / "layout_vis"
    visualizations.mkdir()
    (visualizations / "page-0001.jpg").write_bytes(b"annotated")
    files = tuple(
        ArtifactFile(
            relative_path=path.relative_to(document).as_posix(),
            sha256=file_sha256(path),
            size_bytes=path.stat().st_size,
            media_type="application/octet-stream",
        )
        for path in sorted(item for item in document.rglob("*") if item.is_file())
    )
    result = OcrDocumentResult.model_construct(
        request_id=request.request_id,
        arxiv_id=request.arxiv_id,
        state="succeeded",
        pdf_sha256="d" * 64,
        pdf_size_bytes=100,
        page_count=1,
        processing_id=processing_id(
            arxiv_id=request.arxiv_id,
            pdf_sha256="d" * 64,
            configuration_hash=job.config_hash,
        ),
        manifest_sha256=None,
        error_code=None,
        error_message=None,
        files=files,
    )
    result = OcrDocumentResult.model_validate(
        {**result.model_dump(mode="json"), "manifest_sha256": document_manifest_sha256(result)}
    )
    archive = output / "result.tar.zst"
    _archive(content, archive)
    manifest = OcrBatchManifest(
        schema_version="1.0.0",
        batch_id=job.batch_id,
        created_at=datetime.now(UTC),
        archive_sha256=file_sha256(archive),
        archive_size_bytes=archive.stat().st_size,
        documents=(result,),
    )
    (output / "result_manifest.json").write_text(
        manifest.model_dump_json(),
        encoding="utf-8",
    )


def test_ocr_identity_changes_only_with_output_semantics() -> None:
    processor = load_contracts().processor("arxiv_glm_ocr")
    current = config_hash(processor)
    operational_change = processor.model_copy(
        update={
            "inference": processor.inference.model_copy(update={"api_port": 9000, "max_workers": 2})
        }
    )
    semantic_change = processor.model_copy(
        update={"adapter_version": "1.0.1"},
    )

    assert config_hash(operational_change) == current
    assert config_hash(semantic_change) != current
    assert _job().model_dump_json() == _job().model_dump_json()


def test_completed_runner_output_is_validated_and_safely_extracted(tmp_path: Path) -> None:
    job = _job()
    output = tmp_path / "output"
    output.mkdir()
    _successful_output(output, job)

    manifest = validate_runner_output(output, tmp_path / "extracted", job=job)

    assert manifest.batch_id == job.batch_id
    assert manifest.documents[0].state == "succeeded"


def test_archive_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.zst"
    with (
        archive.open("xb") as raw,
        zstandard.ZstdCompressor().stream_writer(raw) as compressed,
        tarfile.open(fileobj=compressed, mode="w|") as bundle,
    ):
        content = b"escape"
        info = tarfile.TarInfo("../escape")
        info.size = len(content)
        bundle.addfile(info, io.BytesIO(content))

    with pytest.raises(InvalidOcrOutputError, match="Unsafe archive member"):
        extract_archive(archive, tmp_path / "extracted", max_output_bytes=1024)


def test_kaggle_runner_artifact_contains_only_runtime_sources(tmp_path: Path) -> None:
    destination = tmp_path / "runner"

    build_runner(destination)

    package = destination / "mini_lakehouse" / "processing" / "ocr" / "core"
    assert (destination / "bootstrap.py").is_file()
    assert (destination / "runtime.py").is_file()
    assert (package / "protocol.py").is_file()
    assert not (destination / "resource_manifest.json").exists()
    assert not (destination / "progress.py").exists()


def test_kaggle_launcher_uses_declared_runner_and_models() -> None:
    launcher = render_launcher(
        job=_job(),
        runner_dataset_source="owner/runner/versions/1",
        model_source="owner/model/transformers/safetensors/2",
        layout_model_source="owner/layout/transformers/safetensors/3",
    )

    compile(launcher, "launcher.py", "exec")
    assert "kagglehub.dataset_download('owner/runner/versions/1')" in launcher
    assert "kagglehub.model_download('owner/model/transformers/safetensors/2')" in launcher
    assert "kagglehub.model_download('owner/layout/transformers/safetensors/3')" in launcher
    assert "bundle_sha256" not in launcher


class _KaggleApi:
    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}
        self.output_kernel: str | None = None

    def kernels_push(self, folder: str, timeout: str, accelerator: str) -> Any:
        assert int(timeout) > 0
        assert accelerator == "NvidiaTeslaT4"
        self.metadata = json.loads(
            (Path(folder) / "kernel-metadata.json").read_text(encoding="utf-8")
        )
        return SimpleNamespace(
            error=None,
            invalid_dataset_sources=[],
            invalid_kernel_sources=[],
            invalid_model_sources=[],
        )

    def kernels_status(self, kernel: str) -> Any:
        return SimpleNamespace(
            status=SimpleNamespace(name="RUNNING"),
            failure_message=None,
        )

    def kernels_logs(self, kernel: str) -> str:
        return f"{kernel}: running"

    def kernels_output(self, kernel: str, path: str, **_kwargs: object) -> None:
        self.output_kernel = kernel
        destination = Path(path)
        (destination / "result_manifest.json").write_text("{}", encoding="utf-8")
        (destination / "result.tar.zst").write_bytes(b"archive")


def test_kaggle_provider_delegates_to_public_sdk(tmp_path: Path) -> None:
    api = _KaggleApi()
    processor = load_contracts().processor("arxiv_glm_ocr")
    provider = KaggleProvider(
        KaggleSettings.model_validate({"username": "owner", "api_token": "secret"}),
        processor,
        api=api,
    )

    run_id = provider.submit(_job())
    status = provider.status(run_id)
    provider.download_output(run_id, tmp_path / "output")

    assert run_id == "owner/mini-lakehouse-arxiv-glm-ocr"
    assert status.state == OcrProviderState.RUNNING
    assert provider.logs(run_id) == f"{run_id}: running"
    assert api.output_kernel == run_id
    assert api.metadata["dataset_sources"] == ["owner/mini-lakehouse-arxiv-glm-ocr-runner/1"]
    assert api.metadata["model_sources"] == [
        processor.runner.kaggle.model_source,
        processor.runner.kaggle.layout_model_source,
    ]


class _NoopExecutor:
    pass


class _NoopObjectStore:
    pass


class _Repository:
    def __init__(self, active: ActiveOcrBatch | None = None) -> None:
        self.active = active
        self._allow_candidates = active is None
        self.prepared_job = (
            active.job if active is not None and active.state == "prepared" else None
        )
        self.submitted_run_id: str | None = None
        self.document_state: str | None = None
        self.batch_state: str | None = None

    def active_batch(self, _executor: object) -> ActiveOcrBatch | None:
        active, self.active = self.active, None
        return active

    def candidates(self, *_args: object, **_kwargs: object) -> tuple[OcrCandidate, ...]:
        if not self._allow_candidates or self.prepared_job is not None:
            return ()
        return (
            OcrCandidate(
                arxiv_id="2607.00001",
                oai_datestamp=date(2026, 7, 22),
                source_record_sha256=SOURCE_RECORD_SHA256,
                pdf_url="https://arxiv.org/pdf/2607.00001",
                attempt_count=0,
            ),
        )

    def prepare_batch(self, _executor: object, *, job: OcrJob, **_kwargs: object) -> None:
        self.prepared_job = job

    def mark_batch_submitted(
        self,
        _executor: object,
        *,
        batch_id: str,
        provider_run_id: str,
    ) -> None:
        assert self.prepared_job is not None
        assert batch_id == self.prepared_job.batch_id
        self.submitted_run_id = provider_run_id

    def mark_document_failure(self, *_args: object, **kwargs: object) -> None:
        self.document_state = str(kwargs["state"])

    def mark_batch_terminal(self, *_args: object, **kwargs: object) -> None:
        self.batch_state = str(kwargs["state"])


class _Provider:
    name: Literal["kaggle"] = "kaggle"
    reference = "owner/mini-lakehouse-arxiv-glm-ocr"

    def __init__(self, state: OcrProviderState = OcrProviderState.RUNNING) -> None:
        self.state = state
        self.submitted_job: OcrJob | None = None

    def submit(self, job: OcrJob) -> str:
        self.submitted_job = job
        return self.reference

    def status(self, provider_run_id: str) -> OcrRunStatus:
        return OcrRunStatus(
            provider_run_id=provider_run_id,
            state=self.state,
            failure_message="GPU worker failed" if self.state == OcrProviderState.FAILED else None,
        )

    def logs(self, provider_run_id: str) -> str:
        del provider_run_id
        return "remote traceback"

    def download_output(self, provider_run_id: str, destination: Path) -> None:
        del provider_run_id
        destination.mkdir()


def _service(repository: _Repository, provider: _Provider) -> ArxivOcrService:
    return ArxivOcrService(
        Settings(),
        executor=cast(Any, _NoopExecutor()),
        repository=cast(Any, repository),
        provider=provider,
        object_store=cast(Any, _NoopObjectStore()),
    )


def test_ocr_cycle_persists_before_remote_submission() -> None:
    repository = _Repository()
    provider = _Provider()

    result = _service(repository, provider).run_once()

    assert result.action == "submitted"
    assert repository.prepared_job == provider.submitted_job
    assert repository.submitted_run_id == provider.reference


def test_prepared_batch_is_resubmitted_without_private_provider_recovery() -> None:
    job = _job()
    active = ActiveOcrBatch(
        batch_id=job.batch_id,
        state="prepared",
        provider="kaggle",
        provider_reference=_Provider.reference,
        provider_run_id=None,
        job=job,
        documents=(OcrBatchDocument(request=job.documents[0], attempt_count=1),),
    )
    repository = _Repository(active)
    provider = _Provider()

    result = _service(repository, provider).run_once()

    assert result.action == "submitted"
    assert provider.submitted_job == job


def test_stale_prepared_batch_is_failed_without_submission() -> None:
    job = _job(configuration_hash="f" * 64)
    active = ActiveOcrBatch(
        batch_id=job.batch_id,
        state="prepared",
        provider="kaggle",
        provider_reference=_Provider.reference,
        provider_run_id=None,
        job=job,
        documents=(OcrBatchDocument(request=job.documents[0], attempt_count=1),),
    )
    repository = _Repository(active)
    provider = _Provider()

    result = _service(repository, provider).run_once()

    assert result.action == "reconciled"
    assert repository.document_state == "retryable_failed"
    assert repository.batch_state == "failed"
    assert provider.submitted_job is None


def test_exhausted_remote_failure_becomes_terminal() -> None:
    job = _job(attempt_count=3)
    active = ActiveOcrBatch(
        batch_id=job.batch_id,
        state="running",
        provider="kaggle",
        provider_reference=_Provider.reference,
        provider_run_id=_Provider.reference,
        job=job,
        documents=(OcrBatchDocument(request=job.documents[0], attempt_count=3),),
    )
    repository = _Repository(active)

    result = _service(repository, _Provider(OcrProviderState.FAILED)).run_once()

    assert result.terminal_failures == 1
    assert repository.document_state == "terminal_failed"
    assert repository.batch_state == "failed"
