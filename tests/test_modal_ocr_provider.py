import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import modal
import pytest
from document_ocr.config import load_ocr_config
from document_ocr.protocol import OcrDocumentResult, OcrRunResult
from document_ocr.providers.base import OcrProviderRunFailedError
from document_ocr.providers.modal import ModalProvider
from document_ocr.settings import ModalSettings


class _FakeCall:
    object_id = "fc-test"

    def __init__(self, result: object | None = None, *, active: bool = False) -> None:
        self._result = result
        self._active = active
        self.logs = _FakeLogs()

    def get(self, timeout: float | None) -> object:
        if self._active:
            raise modal.exception.TimeoutError("still running")
        return self._result


class _FakeLogEntry:
    def __init__(self, message: str) -> None:
        self.message = message


class _FakeLogs:
    def stream(self):
        yield _FakeLogEntry("runner started\n")


class _FakeFunction:
    def __init__(self, call: _FakeCall) -> None:
        self._call = call
        self.job_json: str | None = None

    def spawn(self, job_json: str) -> _FakeCall:
        self.job_json = job_json
        return self._call


class _FakeVolume:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    def read_file(self, path: str) -> tuple[bytes, ...]:
        return (self._objects[path],)


def _provider() -> ModalProvider:
    return ModalProvider(
        ModalSettings.model_validate({"token_id": "ak-test", "token_secret": "as-test"}),
        load_ocr_config("arxiv_glm_ocr"),
        client=cast(Any, object()),
    )


def test_modal_provider_submits_and_waits_for_the_exact_function_call(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    completed_call = _FakeCall(f"runs/{'b' * 64}")
    function = _FakeFunction(completed_call)
    monkeypatch.setattr(
        modal,
        "Function",
        type("Function", (), {"from_name": staticmethod(lambda *_args, **_kwargs: function)}),
    )
    monkeypatch.setattr(
        modal,
        "FunctionCall",
        type(
            "FunctionCall",
            (),
            {"from_id": staticmethod(lambda *_args, **_kwargs: completed_call)},
        ),
    )
    provider = _provider()
    job = SimpleNamespace(
        model_dump_json=lambda: '{"run_id":"run"}',
    )

    provider_run_id = provider.submit(cast(Any, job))
    logs: list[str] = []
    result = provider.wait(provider_run_id, logs.append)

    assert provider_run_id == "fc-test"
    assert function.job_json == '{"run_id":"run"}'
    assert result is None
    assert logs == ["runner started\n"]


def test_modal_provider_downloads_only_the_committed_protocol_files(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    run_id = "b" * 64
    archive = b"committed archive"
    manifest = (
        OcrRunResult(
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
        .model_dump_json()
        .encode()
    )
    completed_call = _FakeCall(f"runs/{run_id}")
    monkeypatch.setattr(
        modal,
        "FunctionCall",
        type(
            "FunctionCall",
            (),
            {"from_id": staticmethod(lambda *_args, **_kwargs: completed_call)},
        ),
    )
    monkeypatch.setattr(
        modal,
        "Volume",
        type(
            "Volume",
            (),
            {
                "from_name": staticmethod(
                    lambda *_args, **_kwargs: _FakeVolume(
                        {
                            f"runs/{run_id}/result.json": manifest,
                            f"runs/{run_id}/artifacts.tar.zst": archive,
                        }
                    )
                )
            },
        ),
    )
    destination = tmp_path / "output"

    _provider().download_output("fc-test", destination)

    assert (destination / "result.json").read_bytes() == manifest
    assert (destination / "artifacts.tar.zst").read_bytes() == archive


def test_modal_provider_maps_an_invalid_remote_result_to_failed(monkeypatch: Any) -> None:
    call = _FakeCall({"state": "complete"})
    provider = _provider()

    def find_call(_provider_run_id: str) -> Any:
        return call

    monkeypatch.setattr(provider, "_call", find_call)

    with pytest.raises(OcrProviderRunFailedError, match="invalid output prefix"):
        provider.wait("fc-test", lambda _message: None)
