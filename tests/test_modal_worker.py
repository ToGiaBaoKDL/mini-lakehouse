import importlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from document_ocr.artifacts import create_archive
from document_ocr.config import load_config
from document_ocr.identity import (
    canonical_json_bytes,
    canonical_json_sha256,
    file_sha256,
    processing_id,
)
from document_ocr.output import extract_ocr_output
from document_ocr.protocol import (
    OcrError,
    OcrJob,
    OcrOutput,
    OcrReuseReference,
)


def _runtime(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.syspath_prepend(str(Path("ocr-engine/modal").resolve()))
    glmocr = ModuleType("glmocr")
    glmocr.GlmOcr = object  # type: ignore[attr-defined]
    sdk_config = ModuleType("glmocr.config")
    sdk_config.GlmOcrConfig = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "glmocr", glmocr)
    monkeypatch.setitem(sys.modules, "glmocr.config", sdk_config)
    for name in ("server", "worker"):
        sys.modules.pop(name, None)
    return SimpleNamespace(
        server=importlib.import_module("server"),
        worker=importlib.import_module("worker"),
        source=importlib.import_module("document_ocr.source"),
    )


def _job() -> OcrJob:
    config = load_config()
    document_id = "2607.00001"
    pdf_sha256 = "d" * 64
    reuse = OcrReuseReference(
        pdf_sha256=pdf_sha256,
        pdf_size_bytes=100,
        page_count=3,
        processing_id=processing_id(
            document_id=document_id,
            pdf_sha256=pdf_sha256,
            configuration_hash=config.configuration_hash,
        ),
        manifest_sha256="e" * 64,
    )
    return config.job(
        document_id=document_id,
        source_record_sha256="a" * 64,
        pdf_url=f"https://arxiv.org/pdf/{document_id}",
        reuse=reuse,
    )


def test_unchanged_document_commits_reuse_without_running_the_parser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = _runtime(monkeypatch)
    job = _job()
    reuse = job.reuse
    assert reuse is not None

    def download_pdf(_url: str, destination: Path, _maximum: int) -> tuple[str, int]:
        destination.write_bytes(b"%PDF-placeholder")
        return reuse.pdf_sha256, reuse.pdf_size_bytes

    def pdf_page_sizes(_path: Path, _maximum: int) -> tuple[tuple[float, float], ...]:
        return ((612.0, 792.0),) * reuse.page_count

    class _Parser:
        def parse(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("unchanged documents must not run GLM-OCR")

    monkeypatch.setattr(runtime.source, "download_pdf", download_pdf)
    monkeypatch.setattr(runtime.source, "pdf_page_sizes", pdf_page_sizes)

    runtime.worker.run(job, tmp_path, parser=cast(Any, _Parser()))

    result = OcrOutput.model_validate_json((tmp_path / "result.json").read_bytes())
    assert result.state == "reused"


def test_document_error_removes_stale_uncommitted_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = _runtime(monkeypatch)
    (tmp_path / "result.json").write_text("stale", encoding="utf-8")
    (tmp_path / "artifacts.tar.zst").write_text("stale", encoding="utf-8")

    def fail_preparation(*_args: object, **_kwargs: object) -> None:
        raise OcrError("test failure", code="invalid_pdf")

    monkeypatch.setattr(runtime.worker, "prepare_document", fail_preparation)

    with pytest.raises(OcrError, match="invalid_pdf: test failure"):
        runtime.worker.run(_job(), tmp_path, parser=cast(Any, object()))
    assert not (tmp_path / "result.json").exists()
    assert not (tmp_path / "artifacts.tar.zst").exists()


def test_glm_ocr_sdk_config_uses_pinned_prompts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = _runtime(monkeypatch)

    class _SdkConfig:
        def __init__(self, value: dict[str, Any]) -> None:
            self.value = value

        @classmethod
        def from_yaml(cls) -> "_SdkConfig":
            return cls(
                {
                    "pipeline": {
                        "maas": {},
                        "ocr_api": {},
                        "page_loader": {},
                        "layout": {},
                    }
                }
            )

        @classmethod
        def model_validate(cls, value: dict[str, Any]) -> "_SdkConfig":
            return cls(value)

        def to_dict(self) -> dict[str, Any]:
            return self.value

    monkeypatch.setattr(runtime.server, "GlmOcrConfig", _SdkConfig)
    path = tmp_path / "glmocr.yaml"
    runtime.server._sdk_config(path, load_config(), tmp_path / "layout-model")

    configured = runtime.server.yaml.safe_load(path.read_text(encoding="utf-8"))
    assert configured["pipeline"]["page_loader"]["task_prompt_mapping"] == {
        "formula": "Formula Recognition:/nothink",
        "table": "Table Recognition:/nothink",
        "text": "Text Recognition:/nothink",
    }


def test_canonical_elements_accept_sdk_image_with_null_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(monkeypatch)

    elements = runtime.worker._canonical_elements(
        [
            [
                {
                    "index": 6,
                    "label": "image",
                    "content": None,
                    "bbox_2d": [10, 20, 30, 40],
                    "image_path": "imgs/cropped_page0_idx0.jpg",
                }
            ]
        ],
        "f" * 64,
    )

    assert len(elements) == 1
    assert elements[0].element_type == "image"
    assert elements[0].text_content == ""
    assert elements[0].markdown_content == "![Image 0-0](../imgs/cropped_page0_idx0.jpg)"


def test_canonical_elements_reject_null_content_for_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(monkeypatch)

    with pytest.raises(
        OcrError,
        match=r"invalid_model_output: Invalid GLM-OCR block 1:0: content must be text",
    ):
        runtime.worker._canonical_elements(
            [[{"index": 0, "label": "text", "content": None}]],
            "f" * 64,
        )


def test_sdk_save_is_normalized_into_the_shared_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = _runtime(monkeypatch)
    job = _job()
    work = tmp_path / "work"
    work.mkdir()
    pdf = work / "source.pdf"
    pdf.write_bytes(b"%PDF-test")
    prepared = runtime.source.PreparedDocument(
        document_id=job.document_id,
        work_root=work,
        pdf_path=pdf,
        pdf_sha256="f" * 64,
        pdf_size_bytes=9,
        page_sizes=((612.0, 792.0),),
    )
    sdk_options: dict[str, object] = {}

    class _Parsed:
        def __init__(self) -> None:
            self.json_result = [
                [
                    {
                        "label": "text",
                        "content": "Hello",
                        "bbox_2d": [0, 0, 10, 10],
                    }
                ]
            ]

        def save(self, destination: Path, **kwargs: object) -> None:
            sdk_options.update(kwargs)
            layout = destination / "source" / "layout_vis"
            layout.mkdir(parents=True)
            (layout / "page_0.jpg").write_bytes(b"sdk-layout")

    parse_options: dict[str, object] = {}

    def parse(*_args: object, **kwargs: object) -> _Parsed:
        parse_options.update(kwargs)
        return _Parsed()

    output_root = tmp_path / "output"
    manifest = runtime.worker.process_document(
        job,
        prepared,
        cast(Any, SimpleNamespace(parse=parse)),
        output_root,
    )

    assert manifest.document_id == job.document_id
    assert manifest.file("pages.json.gz").size_bytes > 0
    assert parse_options == {"save_layout_visualization": True}
    assert sdk_options == {"save_layout_visualization": True}
    assert (output_root / "layout_vis/page-0001.jpg").read_bytes() == b"sdk-layout"
    assert not (output_root / "manifest.json").exists()

    modal_output = tmp_path / "modal-output"
    modal_output.mkdir()
    archive = modal_output / "artifacts.tar.zst"
    create_archive(output_root, archive)
    (modal_output / "result.json").write_text(
        OcrOutput(
            job_id=job.job_id,
            created_at=datetime(2026, 7, 30, tzinfo=UTC),
            archive_sha256=file_sha256(archive),
            archive_size_bytes=archive.stat().st_size,
            document_id=job.document_id,
            state="succeeded",
            pdf_sha256=prepared.pdf_sha256,
            pdf_size_bytes=prepared.pdf_size_bytes,
            page_count=prepared.page_count,
            processing_id=manifest.processing_id,
            manifest_sha256=canonical_json_sha256(manifest.model_dump(mode="json")),
            manifest=manifest,
        ).model_dump_json(),
        encoding="utf-8",
    )

    imported = extract_ocr_output(modal_output, tmp_path / "extracted", job=job)
    assert imported.processing_id == manifest.processing_id
    assert (tmp_path / "extracted" / "manifest.json").read_bytes() == canonical_json_bytes(
        manifest.model_dump(mode="json")
    )


def test_vllm_command_is_derived_from_the_single_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(monkeypatch)
    command = runtime.server._command(load_config(), Path("/models/model"))

    assert command[:3] == [sys.executable, "-m", "vllm.entrypoints.openai.api_server"]
    assert command[command.index("--served-model-name") + 1] == "glm-ocr"
    assert "--speculative-config" in command
