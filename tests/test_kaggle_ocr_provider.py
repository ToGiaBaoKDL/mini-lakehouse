import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from document_ocr.config import load_ocr_config
from document_ocr.protocol import OcrDocumentResult, OcrRunResult
from document_ocr.providers.base import OcrProviderError
from document_ocr.providers.kaggle import KaggleProvider
from document_ocr.settings import KaggleSettings


def _output(run_id: str) -> dict[str, bytes]:
    archive = b"archive"
    manifest = OcrRunResult(
        run_id=run_id,
        created_at=datetime(2026, 7, 30, tzinfo=UTC),
        archive_sha256=hashlib.sha256(archive).hexdigest(),
        archive_size_bytes=len(archive),
        result=OcrDocumentResult(
            request_id="a" * 64,
            document_id="2607.00001",
            state="reused",
            pdf_sha256="c" * 64,
            pdf_size_bytes=100,
            page_count=1,
            processing_id="d" * 64,
            manifest_sha256="e" * 64,
        ),
    )
    return {
        "result.json": manifest.model_dump_json().encode(),
        "artifacts.tar.zst": archive,
    }


class _KaggleApi:
    def __init__(self, state: str, *, output: dict[str, bytes] | None = None) -> None:
        self.state = state
        self.output = output or {}
        self.output_patterns: list[str] = []
        self.pushes = 0

    def kernels_status(self, _reference: str) -> Any:
        return SimpleNamespace(
            status=SimpleNamespace(name=self.state),
            failure_message=None,
        )

    def kernels_output(
        self,
        _reference: str,
        path: str,
        *,
        file_pattern: str,
        **_kwargs: object,
    ) -> None:
        self.output_patterns.append(file_pattern)
        destination = Path(path)
        names = (
            ("result.json",)
            if file_pattern == r"^result\.json$"
            else ("result.json", "artifacts.tar.zst")
        )
        for name in names:
            payload = self.output.get(name)
            if payload is None:
                continue
            (destination / name).write_bytes(payload)

    def kernels_logs_stream(self, _reference: str):
        yield {"data": "runner started\n"}
        yield {"data": "runner finished\n"}

    def kernels_push(self, path: str, *_args: object) -> Any:
        assert (Path(path) / "launcher.py").is_file()
        assert (Path(path) / "kernel-metadata.json").is_file()
        self.pushes += 1
        return SimpleNamespace(
            error=None,
            invalid_dataset_sources=None,
            invalid_kernel_sources=None,
            invalid_model_sources=None,
        )


def _provider(api: _KaggleApi) -> KaggleProvider:
    return KaggleProvider(
        KaggleSettings.model_validate({"username": "test-user", "api_token": "test-token"}),
        load_ocr_config("arxiv_glm_ocr"),
        api=api,
    )


def _job(run_id: str) -> Any:
    def model_dump_json(**_kwargs: object) -> str:
        return '{"schema_version":"3.0.0"}'

    return cast(
        Any,
        SimpleNamespace(
            run_id=run_id,
            model_dump_json=model_dump_json,
        ),
    )


def test_kaggle_provider_does_not_overwrite_an_active_kernel() -> None:
    api = _KaggleApi("RUNNING")

    with pytest.raises(OcrProviderError, match="already has an active OCR run"):
        _provider(api).submit(_job("b" * 64))

    assert api.pushes == 0


def test_kaggle_provider_reuses_the_committed_run() -> None:
    run_id = "b" * 64
    api = _KaggleApi("COMPLETE", output=_output(run_id))

    provider_run_id = _provider(api).submit(_job(run_id))

    assert provider_run_id == "test-user/document-ocr-arxiv-glm-ocr"
    assert api.pushes == 0
    assert api.output_patterns == [r"^result\.json$"]


def test_kaggle_provider_submits_after_a_different_run_completes() -> None:
    api = _KaggleApi("COMPLETE", output=_output("a" * 64))

    _provider(api).submit(_job("b" * 64))

    assert api.pushes == 1


def test_kaggle_provider_streams_logs_and_returns_terminal_status() -> None:
    api = _KaggleApi("COMPLETE")
    logs: list[str] = []

    result = _provider(api).wait(
        "test-user/document-ocr-arxiv-glm-ocr",
        logs.append,
    )

    assert result is None
    assert logs == ["runner started\n", "runner finished\n"]
