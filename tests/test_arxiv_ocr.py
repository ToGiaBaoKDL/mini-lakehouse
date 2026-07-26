import gzip
import io
import json
import tarfile
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import zstandard

from mini_lakehouse.config.settings import KaggleSettings, Settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.curated_products.arxiv.models import (
    ActiveOcrBatch,
    OcrBatchDocument,
    OcrCandidate,
)
from mini_lakehouse.curated_products.arxiv.ocr_repository import ArxivOcrRepository
from mini_lakehouse.curated_products.arxiv.ocr_service import ArxivOcrService
from mini_lakehouse.platform.trino import QueryResult
from mini_lakehouse.processing.ocr.archive import (
    InvalidOcrOutputError,
    document_manifest_sha256,
    extract_archive,
    validate_runner_output,
)
from mini_lakehouse.processing.ocr.artifacts import artifact_root_uri
from mini_lakehouse.processing.ocr.core.files import file_sha256
from mini_lakehouse.processing.ocr.core.identity import (
    batch_id,
    canonical_json_sha256,
    config_hash,
    processing_id,
    request_id,
)
from mini_lakehouse.processing.ocr.core.paths import runner_document_path
from mini_lakehouse.processing.ocr.core.protocol import (
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
from mini_lakehouse.processing.ocr.kaggle_bundle import (
    MODEL_MANIFEST_NAME,
    KaggleRunnerBundle,
    ModelResourceManifest,
    RunnerResourceManifest,
)
from mini_lakehouse.processing.ocr.kaggle_gateway import KaggleGateway
from mini_lakehouse.processing.ocr.kaggle_provider import KaggleProvider, render_launcher
from mini_lakehouse.processing.ocr.kaggle_resources import KaggleOcrResourceManager
from mini_lakehouse.processing.ocr.kaggle_types import (
    KaggleCurrentRun,
    KaggleGpuQuota,
    KaggleKernelState,
    KaggleResourceNotFoundError,
    KaggleRunStatus,
)
from mini_lakehouse.processing.ocr.text import serialize_plain_text

SOURCE_RECORD_SHA256 = "a" * 64
RUNNER_BUNDLE = KaggleRunnerBundle.load()


def _configuration_hash() -> str:
    return config_hash(
        load_contracts().processor("arxiv_glm_ocr"),
        runner_bundle_sha256=RUNNER_BUNDLE.sha256,
    )


def _job(attempt_count: int = 1) -> OcrJob:
    processor = load_contracts().processor("arxiv_glm_ocr")
    configuration_hash = _configuration_hash()
    request = OcrDocumentRequest(
        request_id=request_id(
            arxiv_id="hep-th/9901001",
            source_record_sha256=SOURCE_RECORD_SHA256,
            configuration_hash=configuration_hash,
        ),
        arxiv_id="hep-th/9901001",
        oai_datestamp=date(2026, 7, 22),
        source_record_sha256=SOURCE_RECORD_SHA256,
        pdf_url="https://arxiv.org/pdf/hep-th/9901001",
    )
    batch_key = batch_id(
        (request.request_id,),
        attempts={request.request_id: attempt_count},
    )
    return OcrJob(
        schema_version="1.0.0",
        batch_id=batch_key,
        runner_bundle_sha256=RUNNER_BUNDLE.sha256,
        model=OcrModel.model_validate(processor.model.model_dump()),
        layout_model=OcrModel.model_validate(processor.layout_model.model_dump()),
        adapter_version=processor.adapter_version,
        output_schema_version=processor.output_schema_version,
        config_hash=configuration_hash,
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


def _successful_output(output: Path, job: OcrJob, *, include_untracked_file: bool = False) -> None:
    request = job.documents[0]
    root = output / "content"
    document = root.joinpath(*runner_document_path(request.arxiv_id, request.request_id).parts)
    document.mkdir(parents=True)
    (document / "document.md").write_text("# Paper\n", encoding="utf-8")
    _gzip(document / "layout.json.gz", b"[]")
    element = OcrElement(
        element_id="e" * 64,
        page_number=1,
        reading_order=0,
        element_type="text",
        text_content="Paper",
        markdown_content="Paper",
    )
    _gzip(document / "elements.jsonl.gz", f"{element.model_dump_json()}\n".encode())
    files = tuple(
        ArtifactFile(
            relative_path=path.relative_to(document).as_posix(),
            sha256=file_sha256(path),
            size_bytes=path.stat().st_size,
            media_type="application/octet-stream",
        )
        for path in sorted(item for item in document.rglob("*") if item.is_file())
    )
    current_processing_id = processing_id(
        arxiv_id=request.arxiv_id,
        pdf_sha256="d" * 64,
        configuration_hash=job.config_hash,
    )
    draft = OcrDocumentResult.model_construct(
        request_id=request.request_id,
        arxiv_id=request.arxiv_id,
        state="succeeded",
        pdf_sha256="d" * 64,
        pdf_size_bytes=100,
        page_count=1,
        processing_id=current_processing_id,
        manifest_sha256=None,
        error_code=None,
        error_message=None,
        files=files,
    )
    result = OcrDocumentResult.model_validate(
        {
            **draft.model_dump(mode="json"),
            "manifest_sha256": document_manifest_sha256(draft),
        }
    )
    if include_untracked_file:
        (root / "untracked.txt").write_text("not declared by the manifest", encoding="utf-8")
    archive = output / "result.tar.zst"
    _archive(root, archive)
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


def test_ocr_identity_and_paths_are_deterministic_and_paper_readable() -> None:
    processor = load_contracts().processor("arxiv_glm_ocr")
    assert processor.inference.enforce_eager is True
    assert processor.inference.max_num_seqs == 1
    assert processor.inference.max_workers == 1
    assert processor.inference.request_timeout_seconds == 600
    assert processor.inference.layout_device == "cpu"
    assert '"wrapt==2.2.2"' in (
        Path("runners/kaggle/glm_ocr/pyproject.toml").read_text(encoding="utf-8")
    )
    first_hash = config_hash(processor, runner_bundle_sha256=RUNNER_BUNDLE.sha256)
    first = request_id(
        arxiv_id="hep-th/9901001",
        source_record_sha256=SOURCE_RECORD_SHA256,
        configuration_hash=first_hash,
    )

    assert first == request_id(
        arxiv_id="hep-th/9901001",
        source_record_sha256=SOURCE_RECORD_SHA256,
        configuration_hash=first_hash,
    )
    assert first != request_id(
        arxiv_id="hep-th/9901001",
        source_record_sha256="b" * 64,
        configuration_hash=first_hash,
    )
    first_processing = processing_id(
        arxiv_id="hep-th/9901001",
        pdf_sha256="d" * 64,
        configuration_hash=first_hash,
    )
    assert first_processing == processing_id(
        arxiv_id="hep-th/9901001",
        pdf_sha256="d" * 64,
        configuration_hash=first_hash,
    )
    assert first_processing != processing_id(
        arxiv_id="2607.00001",
        pdf_sha256="d" * 64,
        configuration_hash=first_hash,
    )
    assert (
        artifact_root_uri(
            Settings(),
            processor,
            arxiv_id="hep-th/9901001",
            processing_id="c" * 64,
        )
        == f"s3://curated/arxiv/ocr/papers/hep-th%2F9901001/{'c' * 64}"
    )
    assert batch_id((first, "a" * 64), attempts={first: 1, "a" * 64: 2}) == batch_id(
        ("a" * 64, first),
        attempts={"a" * 64: 2, first: 1},
    )
    assert batch_id((first,), attempts={first: 1}) != batch_id(
        (first,),
        attempts={first: 2},
    )
    with pytest.raises(ValueError, match="positive value"):
        batch_id((first,), attempts={first: 0})


def test_content_manifest_is_independent_of_request_attempt_identity() -> None:
    files = tuple(
        ArtifactFile(
            relative_path=path,
            sha256=character * 64,
            size_bytes=index,
            media_type="application/octet-stream",
        )
        for index, (path, character) in enumerate(
            (
                ("document.md", "1"),
                ("elements.jsonl.gz", "2"),
                ("layout.json.gz", "3"),
            ),
            start=1,
        )
    )
    first = OcrDocumentResult.model_construct(
        request_id="4" * 64,
        arxiv_id="2607.00001",
        state="succeeded",
        pdf_sha256="5" * 64,
        pdf_size_bytes=100,
        page_count=1,
        processing_id="6" * 64,
        manifest_sha256=None,
        error_code=None,
        error_message=None,
        files=files,
    )
    retried = first.model_copy(update={"request_id": "7" * 64})

    assert document_manifest_sha256(first) == document_manifest_sha256(retried)
    assert document_manifest_sha256(first) != document_manifest_sha256(
        first.model_copy(update={"arxiv_id": "2607.00002"})
    )


def test_plain_text_is_rebuilt_from_page_and_reading_order() -> None:
    elements = (
        OcrElement(
            element_id="b" * 64,
            page_number=2,
            reading_order=0,
            element_type="text",
            text_content="Second page",
        ),
        OcrElement(
            element_id="a" * 64,
            page_number=1,
            reading_order=1,
            element_type="text",
            text_content="Second block",
        ),
        OcrElement(
            element_id="c" * 64,
            page_number=1,
            reading_order=0,
            element_type="title",
            text_content="Title",
        ),
    )

    assert serialize_plain_text(elements) == "Title\n\nSecond block\n\n\f\n\nSecond page"


def test_completed_runner_output_is_validated_and_safely_extracted(tmp_path: Path) -> None:
    job = _job()
    output = tmp_path / "output"
    output.mkdir()
    _successful_output(output, job)

    manifest = validate_runner_output(output, tmp_path / "extracted", job=job)

    assert manifest.batch_id == job.batch_id
    assert manifest.documents[0].state == "succeeded"


def test_runner_output_rejects_files_not_declared_by_the_manifest(tmp_path: Path) -> None:
    job = _job()
    output = tmp_path / "output"
    output.mkdir()
    _successful_output(output, job, include_untracked_file=True)

    with pytest.raises(InvalidOcrOutputError, match="untracked or missing"):
        validate_runner_output(output, tmp_path / "extracted", job=job)


def test_archive_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "malicious.tar.zst"
    with (
        archive.open("xb") as raw,
        zstandard.ZstdCompressor().stream_writer(raw) as compressed,
        tarfile.open(fileobj=compressed, mode="w|") as bundle,
    ):
        content = b"escape"
        info = tarfile.TarInfo("../escape")
        info.size = len(content)
        bundle.addfile(info, io.BytesIO(content))

    try:
        extract_archive(
            archive,
            tmp_path / "extracted",
            max_output_bytes=1024,
        )
    except InvalidOcrOutputError as error:
        assert "Unsafe archive member" in str(error)
    else:
        raise AssertionError("Path traversal archive was accepted")


def test_kaggle_launcher_contains_only_job_and_pinned_runner_reference() -> None:
    launcher = render_launcher(
        job=_job(),
        runner_dataset_source=("owner/mini-lakehouse-arxiv-glm-ocr-runner/versions/4"),
        model_source="owner/mini-lakehouse-glm-ocr/transformers/safetensors/2",
        layout_model_source=("owner/mini-lakehouse-pp-doclayout-v3/transformers/safetensors/3"),
    )

    compile(launcher, "launcher.py", "exec")
    assert (
        "kagglehub.dataset_download('owner/mini-lakehouse-arxiv-glm-ocr-runner/versions/4')"
    ) in launcher
    assert RUNNER_BUNDLE.sha256 in launcher
    assert "mini-lakehouse-glm-ocr/transformers/safetensors/2" in launcher
    assert "mini-lakehouse-pp-doclayout-v3/transformers/safetensors/3" in launcher
    assert "job.json" not in launcher


def test_kaggle_runner_bundle_preserves_the_shared_package_namespace(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "runner"

    RUNNER_BUNDLE.write(destination)

    package = destination / "mini_lakehouse" / "processing" / "ocr" / "core"
    assert (package / "__init__.py").is_file()
    assert (package / "files.py").is_file()
    assert (package / "identity.py").is_file()
    assert (package / "paths.py").is_file()
    assert (package / "protocol.py").is_file()
    assert not (destination / "identity.py").exists()
    assert not (destination / "protocol.py").exists()


def test_kaggle_private_resource_forbidden_is_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        status_code = 403

    class _ForbiddenError(RuntimeError):
        response = _Response()

    gateway = KaggleGateway(
        KaggleSettings.model_validate({"username": "owner", "api_token": "secret"})
    )

    def raise_forbidden() -> None:
        raise _ForbiddenError("private resource is not visible")

    monkeypatch.setattr(gateway, "_api", raise_forbidden)

    with pytest.raises(KaggleResourceNotFoundError):
        gateway.dataset_version("owner/missing-dataset")
    with pytest.raises(KaggleResourceNotFoundError):
        gateway.model_version("owner/missing-model/transformers/default")
    assert gateway.current_kernel_run("owner/missing-kernel") is None


def test_kaggle_log_stream_uses_official_events_and_preserves_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _LogApi:
        def kernels_logs_stream(self, kernel_slug: str) -> Sequence[dict[str, str]]:
            assert kernel_slug == "owner/kernel"
            return (
                {"data": "first line\n"},
                {"data": "second line"},
                {"stream_name": "stdout"},
            )

    gateway = KaggleGateway(
        KaggleSettings.model_validate({"username": "owner", "api_token": "secret"})
    )

    def accept_current_version(owner: str, kernel: str, version: int) -> None:
        assert (owner, kernel, version) == ("owner", "kernel", 7)

    monkeypatch.setattr(
        gateway,
        "_require_current_kernel_version",
        accept_current_version,
    )
    monkeypatch.setattr(gateway, "_api", _LogApi)

    assert tuple(gateway.stream_kernel_logs("owner/kernel/7")) == (
        "first line\n",
        "second line",
    )
    assert gateway.kernel_logs("owner/kernel/7") == "first line\nsecond line\n"


class _ResourceClient:
    def __init__(self, manifest: RunnerResourceManifest | None = None) -> None:
        self.dataset_manifest = manifest
        self.dataset_version_number = 1 if manifest is not None else 0
        self.dataset_uploads = 0
        self.model_manifests: dict[str, ModelResourceManifest] = {}
        self.model_versions: dict[str, int] = {}
        self.model_uploads: dict[str, int] = {}

    def download_dataset_file(
        self,
        dataset_slug: str,
        relative_path: str,
        destination: Path,
    ) -> Path:
        assert dataset_slug == "owner/mini-lakehouse-arxiv-glm-ocr-runner"
        if self.dataset_manifest is None:
            raise KaggleResourceNotFoundError("missing")
        path = destination / relative_path
        path.write_text(self.dataset_manifest.model_dump_json(), encoding="utf-8")
        return path

    def upload_dataset(
        self,
        dataset_slug: str,
        source: Path,
        *,
        version_notes: str,
    ) -> None:
        assert dataset_slug == "owner/mini-lakehouse-arxiv-glm-ocr-runner"
        self.dataset_manifest = RunnerResourceManifest.model_validate_json(
            (source / "resource_manifest.json").read_text(encoding="utf-8")
        )
        assert self.dataset_manifest.bundle_sha256 in version_notes
        self.dataset_version_number += 1
        self.dataset_uploads += 1

    def dataset_version(self, dataset_slug: str) -> int:
        assert dataset_slug == "owner/mini-lakehouse-arxiv-glm-ocr-runner"
        if self.dataset_version_number == 0:
            raise KaggleResourceNotFoundError("missing")
        return self.dataset_version_number

    def download_model_file(
        self,
        model_slug: str,
        relative_path: str,
        destination: Path,
    ) -> Path:
        manifest = self.model_manifests.get(model_slug)
        if manifest is None:
            raise KaggleResourceNotFoundError("missing")
        path = destination / relative_path
        path.write_text(manifest.model_dump_json(), encoding="utf-8")
        return path

    def upload_model(
        self,
        model_slug: str,
        source: Path,
        *,
        license_name: str,
        version_notes: str,
    ) -> None:
        manifest = ModelResourceManifest.model_validate_json(
            (source / MODEL_MANIFEST_NAME).read_text(encoding="utf-8")
        )
        assert (source / "model.safetensors").is_file()
        assert manifest.identity_sha256 in version_notes
        assert license_name in {"Apache 2.0", "MIT"}
        self.model_manifests[model_slug] = manifest
        self.model_versions[model_slug] = self.model_versions.get(model_slug, 0) + 1
        self.model_uploads[model_slug] = self.model_uploads.get(model_slug, 0) + 1

    def model_version(self, model_slug: str) -> int:
        try:
            return self.model_versions[model_slug]
        except KeyError as error:
            raise KaggleResourceNotFoundError("missing") from error


class _SnapshotClient:
    def __init__(self) -> None:
        self.downloads: list[tuple[str, str]] = []

    def download(self, repository: str, revision: str, destination: Path) -> None:
        self.downloads.append((repository, revision))
        destination.mkdir(parents=True)
        (destination / "model.safetensors").write_bytes(b"model")


def _resource_manager(
    client: _ResourceClient,
    snapshots: _SnapshotClient | None = None,
) -> KaggleOcrResourceManager:
    return KaggleOcrResourceManager(
        KaggleSettings.model_validate({"username": "owner", "api_token": "secret"}),
        load_contracts().processor("arxiv_glm_ocr"),
        client,
        bundle=RUNNER_BUNDLE,
        snapshots=snapshots or _SnapshotClient(),
        readiness_attempts=1,
        readiness_delay_seconds=0,
    )


def test_kaggle_runner_resource_reconciliation_is_content_idempotent() -> None:
    client = _ResourceClient()
    resources = _resource_manager(client)

    created = resources.reconcile("runner")
    unchanged = resources.reconcile("runner")

    assert created.action == "created"
    assert created.name == "runner"
    assert created.source.endswith("/versions/1")
    assert unchanged.action == "unchanged"
    assert unchanged.identity_sha256 == RUNNER_BUNDLE.sha256
    assert client.dataset_uploads == 1


def test_kaggle_runner_resource_updates_drifted_content_once() -> None:
    stale_files = {
        **RUNNER_BUNDLE.manifest.files,
        "runtime.py": "f" * 64,
    }
    stale = RunnerResourceManifest(
        schema_version="1.0.0",
        bundle_sha256=canonical_json_sha256({"files": stale_files}),
        files=stale_files,
    )
    client = _ResourceClient(stale)

    result = _resource_manager(client).reconcile("runner")

    assert result.action == "updated"
    assert result.source.endswith("/versions/2")
    assert client.dataset_uploads == 1


def test_kaggle_model_resource_reconciliation_pins_revision_and_is_idempotent() -> None:
    client = _ResourceClient()
    snapshots = _SnapshotClient()
    resources = _resource_manager(client, snapshots)

    created = resources.reconcile("model")
    unchanged = resources.reconcile("model")

    processor = load_contracts().processor("arxiv_glm_ocr")
    assert created.action == "created"
    assert created.source == "owner/mini-lakehouse-glm-ocr/transformers/safetensors/1"
    assert unchanged.action == "unchanged"
    assert snapshots.downloads == [(processor.model.repository, processor.model.revision)]
    assert client.model_uploads[created.source.rsplit("/", 1)[0]] == 1


def test_kaggle_model_resource_retry_recovers_after_remote_publish() -> None:
    class _CrashAfterPublishClient(_ResourceClient):
        should_crash = True

        def upload_model(
            self,
            model_slug: str,
            source: Path,
            *,
            license_name: str,
            version_notes: str,
        ) -> None:
            super().upload_model(
                model_slug,
                source,
                license_name=license_name,
                version_notes=version_notes,
            )
            if self.should_crash:
                self.should_crash = False
                raise RuntimeError("worker stopped after Kaggle accepted the version")

    client = _CrashAfterPublishClient()
    with pytest.raises(RuntimeError, match="worker stopped"):
        _resource_manager(client).reconcile("model")

    recovered = _resource_manager(client).reconcile("model")

    assert recovered.action == "unchanged"
    assert client.model_uploads["owner/mini-lakehouse-glm-ocr/transformers/safetensors"] == 1


def test_kaggle_resource_manager_resolves_only_a_complete_resource_set() -> None:
    client = _ResourceClient()
    resources = _resource_manager(client)
    resources.reconcile("runner")
    resources.reconcile("model")

    with pytest.raises(KaggleResourceNotFoundError, match="gov_arxiv_ocr_resources"):
        resources.resolve_all()

    resources.reconcile("layout_model")
    resolved = resources.resolve_all()

    assert resolved.runner.kind == "dataset"
    assert resolved.model_sources == [
        "owner/mini-lakehouse-glm-ocr/transformers/safetensors/1",
        "owner/mini-lakehouse-pp-doclayout-v3/transformers/safetensors/1",
    ]


class _RunIdentityGateway:
    def __init__(self, source: str) -> None:
        self.source = source
        self.current_run_calls: list[str] = []

    def current_kernel_run(self, kernel_slug: str) -> KaggleCurrentRun:
        assert kernel_slug == "owner/mini-lakehouse-arxiv-glm-ocr"
        self.current_run_calls.append(kernel_slug)
        return KaggleCurrentRun(
            status=KaggleRunStatus(
                provider_run_id="owner/mini-lakehouse-arxiv-glm-ocr/7",
                state=KaggleKernelState.RUNNING,
            ),
            source=self.source,
        )


def test_kaggle_recovery_adopts_only_the_latest_matching_batch_version() -> None:
    job = _job()
    settings = KaggleSettings.model_validate({"username": "owner", "api_token": "secret"})
    processor = load_contracts().processor("arxiv_glm_ocr")
    matching = _RunIdentityGateway(
        render_launcher(
            job=job,
            runner_dataset_source=("owner/mini-lakehouse-arxiv-glm-ocr-runner/versions/1"),
            model_source="owner/model/transformers/default/1",
            layout_model_source="owner/layout/transformers/default/1",
        )
    )
    provider = KaggleProvider(
        settings,
        processor,
        gateway=cast(Any, matching),
        bundle=RUNNER_BUNDLE,
    )

    run = provider.latest_run(job.batch_id)

    assert run is not None
    assert run.provider_run_id.endswith("/7")
    assert matching.current_run_calls == ["owner/mini-lakehouse-arxiv-glm-ocr"]

    unrelated = _RunIdentityGateway("# mini-lakehouse-batch-id: " + "0" * 64 + "\n")
    unrelated_provider = KaggleProvider(
        settings,
        processor,
        gateway=cast(Any, unrelated),
        bundle=RUNNER_BUNDLE,
    )
    assert unrelated_provider.latest_run(job.batch_id) is None
    assert unrelated.current_run_calls == ["owner/mini-lakehouse-arxiv-glm-ocr"]


class _PinnedResourceGateway:
    def __init__(self) -> None:
        self.output_calls: list[tuple[str, str]] = []

    def download_dataset_file(
        self,
        dataset_slug: str,
        relative_path: str,
        destination: Path,
    ) -> Path:
        assert dataset_slug == "owner/mini-lakehouse-arxiv-glm-ocr-runner"
        path = destination / relative_path
        path.write_text(RUNNER_BUNDLE.manifest.model_dump_json(), encoding="utf-8")
        return path

    def dataset_version(self, dataset_slug: str) -> int:
        assert dataset_slug == "owner/mini-lakehouse-arxiv-glm-ocr-runner"
        return 4

    def download_model_file(
        self,
        model_slug: str,
        relative_path: str,
        destination: Path,
    ) -> Path:
        processor = load_contracts().processor("arxiv_glm_ocr")
        if model_slug == "owner/mini-lakehouse-glm-ocr/transformers/safetensors":
            name = "model"
            model = processor.model
        elif model_slug == ("owner/mini-lakehouse-pp-doclayout-v3/transformers/safetensors"):
            name = "layout_model"
            model = processor.layout_model
        else:
            raise AssertionError(f"Unexpected model slug {model_slug}")
        identity = canonical_json_sha256(
            {
                "repository": model.repository,
                "resource_name": name,
                "revision": model.revision,
            }
        )
        path = destination / relative_path
        path.write_text(
            ModelResourceManifest(
                schema_version="1.0.0",
                resource_name=cast(Any, name),
                repository=model.repository,
                revision=model.revision,
                identity_sha256=identity,
            ).model_dump_json(),
            encoding="utf-8",
        )
        return path

    def model_version(self, model_slug: str) -> int:
        if model_slug.endswith("/mini-lakehouse-glm-ocr/transformers/safetensors"):
            return 2
        if model_slug.endswith("/mini-lakehouse-pp-doclayout-v3/transformers/safetensors"):
            return 3
        raise AssertionError(f"Unexpected model slug {model_slug}")

    def download_notebook_file(
        self,
        provider_run_id: str,
        relative_path: str,
        destination: Path,
    ) -> Path:
        self.output_calls.append((provider_run_id, relative_path))
        path = destination / relative_path
        path.write_bytes(b"output")
        return path


def test_kaggle_submission_and_output_pin_resource_and_run_versions(tmp_path: Path) -> None:
    gateway = _PinnedResourceGateway()
    processor = load_contracts().processor("arxiv_glm_ocr")
    provider = KaggleProvider(
        KaggleSettings.model_validate({"username": "owner", "api_token": "secret"}),
        processor,
        gateway=cast(Any, gateway),
        bundle=RUNNER_BUNDLE,
    )
    submission = tmp_path / "submission"

    provider.prepare_submission(submission, _job())
    metadata = json.loads((submission / "kernel-metadata.json").read_text(encoding="utf-8"))
    provider.download_output(
        "owner/mini-lakehouse-arxiv-glm-ocr/17",
        tmp_path / "output",
    )

    assert sorted(path.name for path in submission.iterdir()) == [
        "kernel-metadata.json",
        "launcher.py",
    ]
    assert metadata["dataset_sources"] == ["owner/mini-lakehouse-arxiv-glm-ocr-runner"]
    assert metadata["model_sources"] == [
        "owner/mini-lakehouse-glm-ocr/transformers/safetensors/2",
        "owner/mini-lakehouse-pp-doclayout-v3/transformers/safetensors/3",
    ]
    launcher = (submission / "launcher.py").read_text(encoding="utf-8")
    assert metadata["model_sources"][0] in launcher
    assert metadata["model_sources"][1] in launcher
    assert gateway.output_calls == [
        ("owner/mini-lakehouse-arxiv-glm-ocr/17", "result_manifest.json"),
        ("owner/mini-lakehouse-arxiv-glm-ocr/17", "result.tar.zst"),
    ]


class _NoopExecutor:
    pass


class _SqlCapture:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...] | None]] = []

    def execute(
        self,
        statement: str,
        parameters: Sequence[Any] | None = None,
    ) -> QueryResult:
        self.calls.append((statement, tuple(parameters) if parameters is not None else None))
        return QueryResult(columns=(), rows=())


def test_ocr_repository_publishes_the_batch_commit_marker_last() -> None:
    job = _job()
    documents = (OcrBatchDocument(request=job.documents[0], attempt_count=1),)
    executor = _SqlCapture()
    repository = ArxivOcrRepository(Settings())

    repository.prepare_batch(
        executor,
        job=job,
        documents=documents,
        kernel_slug="owner/kernel",
    )

    assert len(executor.calls) == 2
    assert '"ocr_document_runs"' in executor.calls[0][0]
    assert "target.batch_id = source.batch_id" in executor.calls[0][0]
    assert '"ocr_batches"' in executor.calls[-1][0]
    assert "job_json" in executor.calls[-1][0]
    assert executor.calls[-1][1] is not None
    assert executor.calls[-1][1][-1] == job.model_dump_json()
    assert all(
        statement.count("?") == len(parameters or ()) for statement, parameters in executor.calls
    )


class _ActiveBatchExecutor:
    def __init__(self, job: OcrJob, *, raw_job: str | None = None) -> None:
        request = job.documents[0]
        self.rows = (
            (
                job.batch_id,
                "running",
                "owner/kernel/7",
                1,
                raw_job or job.model_dump_json(),
                request.request_id,
                request.arxiv_id,
                request.oai_datestamp,
                request.source_record_sha256,
                request.pdf_url,
                1,
                "running",
            ),
        )

    def execute(
        self,
        statement: str,
        parameters: Sequence[Any] | None = None,
    ) -> QueryResult:
        del statement, parameters
        return QueryResult(columns=(), rows=self.rows)


def test_active_ocr_batch_restores_its_immutable_job_payload() -> None:
    job = _job()

    active = ArxivOcrRepository(Settings()).active_batch(_ActiveBatchExecutor(job))

    assert active is not None
    assert active.job == job
    assert active.documents[0].request == job.documents[0]


def test_active_ocr_batch_restores_schema_v1_inference_defaults() -> None:
    current_job = _job()
    legacy_payload = current_job.model_dump(mode="json")
    legacy_payload["inference"]["dtype"] = "half"
    del legacy_payload["inference"]["enforce_eager"]

    active = ArxivOcrRepository(Settings()).active_batch(
        _ActiveBatchExecutor(current_job, raw_job=json.dumps(legacy_payload))
    )

    assert active is not None
    assert active.job.inference.dtype == "half"
    assert active.job.inference.enforce_eager is False


def test_ocr_candidates_use_config_identity_and_ignore_orphan_attempts() -> None:
    processor = load_contracts().processor("arxiv_glm_ocr")
    executor = _SqlCapture()
    repository = ArxivOcrRepository(Settings())

    assert (
        repository.candidates(
            executor,
            processor,
            configuration_hash=_configuration_hash(),
            limit=1,
        )
        == ()
    )

    statement, parameters = executor.calls[0]
    assert 'JOIN "prod"."curated.arxiv"."ocr_batches"' in statement
    assert "row_number() OVER" in statement
    assert "document.source_record_sha256 = paper.source_record_sha256" in statement
    assert "document.model_revision = ?" not in statement
    assert parameters == (_configuration_hash(), processor.retry.max_document_attempts)
    assert parameters is not None
    assert statement.count("?") == len(parameters)


def test_document_state_is_committed_before_batch_state() -> None:
    executor = _SqlCapture()
    repository = ArxivOcrRepository(Settings())

    repository.mark_batch_submitted(
        executor,
        batch_id="b" * 64,
        provider_run_id="owner/kernel/1",
    )

    assert len(executor.calls) == 2
    assert '"ocr_document_runs"' in executor.calls[0][0]
    assert '"ocr_batches"' in executor.calls[1][0]


class _NoopTableManager:
    def ensure_tables(self, _executor: object) -> None:
        pass


class _NoopObjectStore:
    def exists(self, _uri: str) -> bool:
        return False

    def upload(self, _source: Path, _destination_uri: str) -> None:
        pass

    def upload_if_absent(self, _source: Path, _destination_uri: str) -> bool:
        return True

    def download(self, _source_uri: str, _destination: Path) -> None:
        pass


class _SubmissionProvider:
    kernel_slug = "owner/mini-lakehouse-arxiv-glm-ocr"
    runner_bundle_sha256 = RUNNER_BUNDLE.sha256

    def __init__(self) -> None:
        self.submitted_job: OcrJob | None = None
        self.status_run_ids: list[str] = []

    def prepare_submission(self, destination: Path, job: OcrJob) -> None:
        destination.mkdir()
        self.submitted_job = job

    def submit(self, submission_directory: Path) -> str:
        del submission_directory
        return f"{self.kernel_slug}/1"

    def latest_run(self, batch_id: str) -> None:
        del batch_id
        return None

    def status(self, provider_run_id: str) -> KaggleRunStatus:
        self.status_run_ids.append(provider_run_id)
        return KaggleRunStatus(
            provider_run_id=provider_run_id,
            state=KaggleKernelState.RUNNING,
        )

    def logs(self, provider_run_id: str) -> str:
        del provider_run_id
        return ""

    def stream_logs(self, provider_run_id: str) -> Iterator[str]:
        del provider_run_id
        return iter(())

    def gpu_quota(self) -> KaggleGpuQuota:
        return KaggleGpuQuota(remaining_minutes=120)

    def download_output(self, provider_run_id: str, destination: Path) -> None:
        del provider_run_id, destination
        raise AssertionError("No output should be downloaded while submitting")


class _SubmissionRepository:
    def __init__(self) -> None:
        self.prepared_job: OcrJob | None = None
        self.submitted_run_id: str | None = None

    def active_batch(self, _executor: object) -> None:
        return None

    def candidates(self, *_args: object, **_kwargs: object) -> tuple[OcrCandidate, ...]:
        if self.prepared_job is not None:
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

    def prepare_batch(
        self,
        _executor: object,
        *,
        job: OcrJob,
        documents: tuple[OcrBatchDocument, ...],
        kernel_slug: str,
    ) -> None:
        assert len(documents) == 1
        assert kernel_slug == _SubmissionProvider.kernel_slug
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


class _IdleRepository:
    def active_batch(self, _executor: object) -> None:
        return None

    def candidates(self, *_args: object, **_kwargs: object) -> tuple[OcrCandidate, ...]:
        return ()


def test_idle_ocr_cycle_does_not_require_kaggle_credentials() -> None:
    service = ArxivOcrService(
        Settings(),
        executor=cast(Any, _NoopExecutor()),
        table_manager=cast(Any, _NoopTableManager()),
        repository=cast(Any, _IdleRepository()),
        object_store=cast(Any, _NoopObjectStore()),
    )

    assert service.run_once().action == "idle"


def test_ocr_cycle_prepares_durable_state_before_remote_submission() -> None:
    repository = _SubmissionRepository()
    provider = _SubmissionProvider()
    service = ArxivOcrService(
        Settings(),
        executor=cast(Any, _NoopExecutor()),
        table_manager=cast(Any, _NoopTableManager()),
        repository=cast(Any, repository),
        provider=provider,
        object_store=cast(Any, _NoopObjectStore()),
    )

    result = service.run_once(batch_size=1)

    assert result.action == "submitted"
    assert repository.prepared_job == provider.submitted_job
    assert repository.submitted_run_id == f"{provider.kernel_slug}/1"


class _QuotaExhaustedProvider(_SubmissionProvider):
    def gpu_quota(self) -> KaggleGpuQuota:
        return KaggleGpuQuota(
            remaining_minutes=15,
            refresh_at=datetime(2026, 7, 27, tzinfo=UTC),
        )


def test_ocr_cycle_defers_before_durable_prepare_when_gpu_quota_is_low() -> None:
    repository = _SubmissionRepository()
    service = ArxivOcrService(
        Settings(),
        executor=cast(Any, _NoopExecutor()),
        table_manager=cast(Any, _NoopTableManager()),
        repository=cast(Any, repository),
        provider=_QuotaExhaustedProvider(),
        object_store=cast(Any, _NoopObjectStore()),
    )

    result = service.run_once(batch_size=1)

    assert result.action == "deferred_quota"
    assert result.remaining_gpu_quota_minutes == 15
    assert result.quota_refresh_at == datetime(2026, 7, 27, tzinfo=UTC)
    assert repository.prepared_job is None


class _FailedRepository:
    def __init__(self, active: ActiveOcrBatch) -> None:
        self.active = active
        self.document_state: str | None = None
        self.batch_state: str | None = None

    def active_batch(self, _executor: object) -> ActiveOcrBatch | None:
        active, self.active = self.active, cast(Any, None)
        return active

    def candidates(self, *_args: object, **_kwargs: object) -> tuple[OcrCandidate, ...]:
        return ()

    def mark_document_failure(self, *_args: object, **kwargs: object) -> None:
        self.document_state = cast(str, kwargs["state"])

    def mark_batch_terminal(self, *_args: object, **kwargs: object) -> None:
        self.batch_state = cast(str, kwargs["state"])


class _FailedProvider(_SubmissionProvider):
    def status(self, provider_run_id: str) -> KaggleRunStatus:
        self.status_run_ids.append(provider_run_id)
        return KaggleRunStatus(
            provider_run_id=provider_run_id,
            state=KaggleKernelState.FAILED,
            failure_message="GPU worker failed",
        )

    def logs(self, provider_run_id: str) -> str:
        del provider_run_id
        return "remote traceback"


class _InvalidOutputProvider(_SubmissionProvider):
    def status(self, provider_run_id: str) -> KaggleRunStatus:
        self.status_run_ids.append(provider_run_id)
        return KaggleRunStatus(
            provider_run_id=provider_run_id,
            state=KaggleKernelState.COMPLETE,
        )

    def download_output(self, provider_run_id: str, destination: Path) -> None:
        del provider_run_id
        destination.mkdir()


def test_exhausted_remote_failure_becomes_terminal_without_resubmission() -> None:
    job = _job(attempt_count=3)
    active = ActiveOcrBatch(
        batch_id=job.batch_id,
        state="running",
        provider_run_id="owner/mini-lakehouse-arxiv-glm-ocr/1",
        job=job,
        documents=(OcrBatchDocument(request=job.documents[0], attempt_count=3),),
    )
    repository = _FailedRepository(active)
    provider = _FailedProvider()
    service = ArxivOcrService(
        Settings(),
        executor=cast(Any, _NoopExecutor()),
        table_manager=cast(Any, _NoopTableManager()),
        repository=cast(Any, repository),
        provider=provider,
        object_store=cast(Any, _NoopObjectStore()),
    )

    result = service.run_once()

    assert result.action == "reconciled"
    assert result.terminal_failures == 1
    assert repository.document_state == "terminal_failed"
    assert repository.batch_state == "failed"
    assert provider.status_run_ids == ["owner/mini-lakehouse-arxiv-glm-ocr/1"]


def test_invalid_remote_output_becomes_a_typed_retryable_attempt() -> None:
    job = _job()
    active = ActiveOcrBatch(
        batch_id=job.batch_id,
        state="running",
        provider_run_id="owner/mini-lakehouse-arxiv-glm-ocr/1",
        job=job,
        documents=(OcrBatchDocument(request=job.documents[0], attempt_count=1),),
    )
    repository = _FailedRepository(active)
    service = ArxivOcrService(
        Settings(),
        executor=cast(Any, _NoopExecutor()),
        table_manager=cast(Any, _NoopTableManager()),
        repository=cast(Any, repository),
        provider=_InvalidOutputProvider(),
        object_store=cast(Any, _NoopObjectStore()),
    )

    result = service.run_once()

    assert result.action == "reconciled"
    assert result.retryable_failures == 1
    assert repository.document_state == "retryable_failed"
    assert repository.batch_state == "failed"


def test_remote_batch_failure_never_downgrades_an_imported_document() -> None:
    job = _job()
    active = ActiveOcrBatch(
        batch_id=job.batch_id,
        state="running",
        provider_run_id="owner/mini-lakehouse-arxiv-glm-ocr/1",
        job=job,
        documents=(
            OcrBatchDocument(
                request=job.documents[0],
                attempt_count=1,
                state="imported",
            ),
        ),
    )
    repository = _FailedRepository(active)
    service = ArxivOcrService(
        Settings(),
        executor=cast(Any, _NoopExecutor()),
        table_manager=cast(Any, _NoopTableManager()),
        repository=cast(Any, repository),
        provider=_FailedProvider(),
        object_store=cast(Any, _NoopObjectStore()),
    )

    result = service.run_once()

    assert result.action == "reconciled"
    assert result.retryable_failures == 0
    assert result.terminal_failures == 0
    assert repository.document_state is None
    assert repository.batch_state == "failed"


def test_prepared_batch_from_an_old_configuration_is_released_for_a_new_batch() -> None:
    current_job = _job()
    old_configuration_hash = "f" * 64
    current_request = current_job.documents[0]
    old_request = current_request.model_copy(
        update={
            "request_id": request_id(
                arxiv_id=current_request.arxiv_id,
                source_record_sha256=current_request.source_record_sha256,
                configuration_hash=old_configuration_hash,
            )
        }
    )
    old_batch_id = batch_id(
        (old_request.request_id,),
        attempts={old_request.request_id: 1},
    )
    old_job = OcrJob.model_validate(
        {
            **current_job.model_dump(),
            "batch_id": old_batch_id,
            "config_hash": old_configuration_hash,
            "documents": (old_request,),
        }
    )
    active = ActiveOcrBatch(
        batch_id=old_job.batch_id,
        state="prepared",
        provider_run_id=None,
        job=old_job,
        documents=(OcrBatchDocument(request=old_job.documents[0], attempt_count=1),),
    )
    repository = _FailedRepository(active)
    provider = _SubmissionProvider()
    service = ArxivOcrService(
        Settings(),
        executor=cast(Any, _NoopExecutor()),
        table_manager=cast(Any, _NoopTableManager()),
        repository=cast(Any, repository),
        provider=provider,
        object_store=cast(Any, _NoopObjectStore()),
    )

    result = service.run_once()

    assert result.action == "reconciled"
    assert repository.document_state == "retryable_failed"
    assert repository.batch_state == "failed"
    assert provider.submitted_job is None
