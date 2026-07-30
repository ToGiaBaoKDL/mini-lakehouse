import importlib
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from document_ocr.archive import extract_ocr_output
from document_ocr.config import load_ocr_config
from document_ocr.identity import (
    processing_id,
    request_id,
)
from document_ocr.protocol import (
    OcrDocumentManifest,
    OcrDocumentRequest,
    OcrJob,
    OcrReuseReference,
    OcrRunManifest,
)


def _runner_modules(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    fitz = ModuleType("fitz")
    glmocr = ModuleType("glmocr")
    glmocr.GlmOcr = object  # type: ignore[attr-defined]
    config = ModuleType("glmocr.config")
    config.GlmOcrConfig = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fitz", fitz)
    monkeypatch.setitem(sys.modules, "glmocr", glmocr)
    monkeypatch.setitem(sys.modules, "glmocr.config", config)
    module_names = (
        "ocr.runners.glm_ocr.runner.document",
        "ocr.runners.glm_ocr.runner.engine",
        "ocr.runners.glm_ocr.runner.job",
    )
    for name in module_names:
        sys.modules.pop(name, None)
    return SimpleNamespace(
        document=importlib.import_module(module_names[0]),
        engine=importlib.import_module(module_names[1]),
        job=importlib.import_module(module_names[2]),
    )


def _job() -> OcrJob:
    processor = load_ocr_config("arxiv_glm_ocr")
    configuration_hash = processor.configuration_hash
    document_id = "2607.00001"
    pdf_sha256 = "d" * 64
    reuse = OcrReuseReference(
        pdf_sha256=pdf_sha256,
        pdf_size_bytes=100,
        page_count=3,
        processing_id=processing_id(
            document_id=document_id,
            pdf_sha256=pdf_sha256,
            configuration_hash=configuration_hash,
        ),
        manifest_sha256="e" * 64,
    )
    request = OcrDocumentRequest(
        request_id=request_id(
            document_id=document_id,
            source_record_sha256="a" * 64,
            configuration_hash=configuration_hash,
        ),
        document_id=document_id,
        source_updated_date=date(2026, 7, 27),
        source_record_sha256="a" * 64,
        pdf_url=f"https://arxiv.org/pdf/{document_id}",
        reuse=reuse,
    )
    return processor.build_job(request, attempt=1)


def test_unchanged_document_skips_model_validation_and_vllm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner_modules(monkeypatch)
    job = _job()
    reuse = job.document.reuse
    assert reuse is not None

    def download_pdf(_request: object, destination: Path, _maximum: int) -> tuple[str, int]:
        destination.write_bytes(b"%PDF-placeholder")
        return reuse.pdf_sha256, reuse.pdf_size_bytes

    def pdf_page_count(_path: Path, _maximum: int) -> int:
        return reuse.page_count

    monkeypatch.setattr(runner.document, "download_pdf", download_pdf)
    monkeypatch.setattr(runner.document, "pdf_page_count", pdf_page_count)

    runner.job.run(
        job,
        tmp_path,
        model_path=tmp_path / "missing-model",
        layout_model_path=tmp_path / "missing-layout-model",
    )

    manifest = OcrRunManifest.model_validate_json((tmp_path / "result_manifest.json").read_bytes())
    assert manifest.result.state == "reused"


def test_unexpected_preparation_error_aborts_the_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner_modules(monkeypatch)
    (tmp_path / "result_manifest.json").write_text("stale", encoding="utf-8")

    def fail_preparation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("runner bug")

    monkeypatch.setattr(runner.job, "prepare_document", fail_preparation)

    with pytest.raises(AssertionError, match="runner bug"):
        runner.job.run(
            _job(),
            tmp_path,
            model_path=tmp_path / "model",
            layout_model_path=tmp_path / "layout-model",
        )
    assert not (tmp_path / "result_manifest.json").exists()


def test_changed_pdf_is_prepared_for_inference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner_modules(monkeypatch)
    job = _job()
    request = job.document
    work = tmp_path / "work"
    work.mkdir()

    def download_pdf(_request: object, destination: Path, _maximum: int) -> tuple[str, int]:
        destination.write_bytes(b"%PDF-changed")
        return "f" * 64, 120

    def pdf_page_count(_path: Path, _maximum: int) -> int:
        return 4

    monkeypatch.setattr(runner.document, "download_pdf", download_pdf)
    monkeypatch.setattr(runner.document, "pdf_page_count", pdf_page_count)

    prepared = runner.document.prepare_document(
        job,
        request,
        work,
    )

    assert isinstance(prepared, runner.document.PreparedDocument)
    assert prepared.pdf_sha256 == "f" * 64
    assert prepared.page_count == 4


def test_successful_document_publishes_the_shared_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner_modules(monkeypatch)
    job = _job()
    request = job.document
    work = tmp_path / "work"
    work.mkdir()
    pdf = work / "source.pdf"
    pdf.write_bytes(b"%PDF-test")
    prepared = runner.document.PreparedDocument(
        request=request,
        work_root=work,
        pdf_path=pdf,
        pdf_sha256="f" * 64,
        pdf_size_bytes=9,
        page_count=1,
    )

    class _Image:
        def save(self, path: Path, **_kwargs: object) -> None:
            path.write_bytes(b"image")

    parsed = SimpleNamespace(
        json_result=[
            [
                {
                    "label": "text",
                    "content": "Hello",
                    "bbox_2d": [0, 0, 10, 10],
                }
            ]
        ],
        layout_vis_images={0: _Image()},
        image_files={},
    )

    def parse(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return parsed

    parser = SimpleNamespace(parse=parse)

    output_root = tmp_path / "output"
    result = runner.document.process_document(
        job,
        prepared,
        parser,
        output_root,
    )

    manifest_path = tmp_path / "output" / "manifest.json"
    manifest = OcrDocumentManifest.model_validate_json(manifest_path.read_bytes())
    assert manifest.document_id == request.document_id
    assert manifest.processing_id == result.processing_id
    assert manifest.file("pages.json.gz").size_bytes > 0
    assert result.manifest_sha256 == runner.document.file_sha256(manifest_path)

    provider_output = tmp_path / "provider-output"
    provider_output.mkdir()
    archive = provider_output / "result.tar.zst"
    runner.job._create_archive(output_root, archive)
    (provider_output / "result_manifest.json").write_text(
        OcrRunManifest(
            run_id=job.run_id,
            created_at=datetime(2026, 7, 30, tzinfo=UTC),
            archive_sha256=runner.document.file_sha256(archive),
            archive_size_bytes=archive.stat().st_size,
            result=result,
        ).model_dump_json(),
        encoding="utf-8",
    )

    imported = extract_ocr_output(
        provider_output,
        tmp_path / "extracted",
        job=job,
    )
    assert imported.result == result


def test_compatible_inference_engine_reuses_one_vllm_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner_modules(monkeypatch)
    starts: list[object] = []
    stops: list[object] = []

    class _Process:
        def poll(self) -> None:
            return None

    class _Parser:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "_Parser":
            return self

        def __exit__(self, *_args: object) -> None:
            pass

    process = _Process()
    log_file = object()
    monkeypatch.setattr(runner.engine, "GlmOcr", _Parser)

    def write_config(*_args: object, **_kwargs: object) -> None:
        pass

    monkeypatch.setattr(runner.engine, "write_config", write_config)

    def start(*_args: object, **_kwargs: object) -> tuple[object, object]:
        starts.append(process)
        return process, log_file

    def stop(*_args: object, **_kwargs: object) -> None:
        stops.append(process)

    monkeypatch.setattr(runner.engine, "start_vllm", start)
    monkeypatch.setattr(runner.engine, "stop_process", stop)
    engine = runner.engine.InferenceEngine(tmp_path / "engine")
    job = _job()
    model_path = tmp_path / "model"
    layout_path = tmp_path / "layout"

    first = engine.acquire(
        job,
        model_path=model_path,
        layout_model_path=layout_path,
    )
    second = engine.acquire(
        job,
        model_path=model_path,
        layout_model_path=layout_path,
    )
    engine.close()

    assert first is second
    assert starts == [process]
    assert stops == [process]
