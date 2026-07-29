import importlib
import sys
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from document_ocr.config import load_ocr_config
from document_ocr.identity import (
    batch_id,
    config_hash,
    processing_id,
    request_id,
)
from document_ocr.protocol import (
    OCR_OUTPUT_SCHEMA_VERSION,
    OcrBatchManifest,
    OcrDocumentRequest,
    OcrInference,
    OcrJob,
    OcrLimits,
    OcrModel,
    OcrReuseReference,
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
        "ocr.runners.glm_ocr.runner.documents",
        "ocr.runners.glm_ocr.runner.engine",
        "ocr.runners.glm_ocr.runner.batch",
    )
    for name in module_names:
        sys.modules.pop(name, None)
    return SimpleNamespace(
        documents=importlib.import_module(module_names[0]),
        engine=importlib.import_module(module_names[1]),
        batch=importlib.import_module(module_names[2]),
    )


def _job() -> OcrJob:
    processor = load_ocr_config("arxiv_glm_ocr")
    configuration_hash = config_hash(processor)
    arxiv_id = "2607.00001"
    pdf_sha256 = "d" * 64
    reuse = OcrReuseReference(
        pdf_sha256=pdf_sha256,
        pdf_size_bytes=100,
        page_count=3,
        processing_id=processing_id(
            arxiv_id=arxiv_id,
            pdf_sha256=pdf_sha256,
            configuration_hash=configuration_hash,
        ),
        manifest_sha256="e" * 64,
    )
    request = OcrDocumentRequest(
        request_id=request_id(
            arxiv_id=arxiv_id,
            source_record_sha256="a" * 64,
            configuration_hash=configuration_hash,
        ),
        arxiv_id=arxiv_id,
        oai_datestamp=date(2026, 7, 27),
        source_record_sha256="a" * 64,
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        reuse=reuse,
    )
    request_attempts = {request.request_id: 1}
    return OcrJob(
        schema_version="1.0.0",
        batch_id=batch_id((request.request_id,), attempts=request_attempts),
        model=OcrModel.model_validate(processor.model.model_dump()),
        layout_model=OcrModel.model_validate(processor.layout_model.model_dump()),
        adapter_version=processor.adapter_version,
        output_schema_version=OCR_OUTPUT_SCHEMA_VERSION,
        config_hash=configuration_hash,
        limits=OcrLimits(
            max_pdf_bytes=processor.batch.max_pdf_bytes,
            max_pages_per_document=processor.batch.max_pages_per_document,
            max_output_bytes=processor.batch.max_output_bytes,
        ),
        inference=OcrInference.model_validate(processor.inference.model_dump()),
        documents=(request,),
    )


def test_unchanged_batch_skips_model_validation_and_vllm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner_modules(monkeypatch)
    job = _job()
    reuse = job.documents[0].reuse
    assert reuse is not None

    def download_pdf(_request: object, destination: Path, _maximum: int) -> tuple[str, int]:
        destination.write_bytes(b"%PDF-placeholder")
        return reuse.pdf_sha256, reuse.pdf_size_bytes

    def pdf_page_count(_path: Path, _maximum: int) -> int:
        return reuse.page_count

    monkeypatch.setattr(runner.documents, "download_pdf", download_pdf)
    monkeypatch.setattr(runner.documents, "pdf_page_count", pdf_page_count)

    runner.batch.run(
        job,
        tmp_path,
        model_path=tmp_path / "missing-model",
        layout_model_path=tmp_path / "missing-layout-model",
    )

    manifest = OcrBatchManifest.model_validate_json(
        (tmp_path / "result_manifest.json").read_bytes()
    )
    assert manifest.documents[0].state == "reused"
    assert manifest.documents[0].files == ()


def test_unexpected_preparation_error_aborts_the_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _runner_modules(monkeypatch)
    (tmp_path / "result_manifest.json").write_text("stale", encoding="utf-8")

    def fail_preparation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("runner bug")

    monkeypatch.setattr(runner.batch, "prepare_document", fail_preparation)

    with pytest.raises(AssertionError, match="runner bug"):
        runner.batch.run(
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
    request = job.documents[0]
    work = tmp_path / "work"
    work.mkdir()

    def download_pdf(_request: object, destination: Path, _maximum: int) -> tuple[str, int]:
        destination.write_bytes(b"%PDF-changed")
        return "f" * 64, 120

    def pdf_page_count(_path: Path, _maximum: int) -> int:
        return 4

    monkeypatch.setattr(runner.documents, "download_pdf", download_pdf)
    monkeypatch.setattr(runner.documents, "pdf_page_count", pdf_page_count)

    prepared = runner.documents.prepare_document(
        job,
        request,
        work,
    )

    assert isinstance(prepared, runner.documents.PreparedDocument)
    assert prepared.pdf_sha256 == "f" * 64
    assert prepared.page_count == 4


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
