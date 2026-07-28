from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import modal

from mini_lakehouse.config.settings import ModalSettings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.processing.ocr.modal_provider import ModalProvider
from mini_lakehouse.processing.ocr.provider import OcrProviderState


class _FakeCall:
    object_id = "fc-test"

    def __init__(self, result: object | None = None, *, active: bool = False) -> None:
        self._result = result
        self._active = active

    def get(self, timeout: float) -> object:
        assert timeout == 0
        if self._active:
            raise modal.exception.TimeoutError("still running")
        return self._result


class _FakeFunction:
    def __init__(self, call: _FakeCall) -> None:
        self._call = call
        self.job_json: str | None = None

    def spawn(self, job_json: str) -> _FakeCall:
        self.job_json = job_json
        return self._call


class _FakeVolume:
    def read_file(self, path: str) -> tuple[bytes, ...]:
        return (path.encode(),)


def _provider() -> ModalProvider:
    return ModalProvider(
        ModalSettings.model_validate({"token_id": "ak-test", "token_secret": "as-test"}),
        load_contracts().processor("arxiv_glm_ocr"),
        client=cast(Any, object()),
    )


def test_modal_provider_submits_and_polls_the_exact_function_call(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    active_call = _FakeCall(active=True)
    function = _FakeFunction(active_call)
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
            {"from_id": staticmethod(lambda *_args, **_kwargs: active_call)},
        ),
    )
    provider = _provider()
    job = SimpleNamespace(
        model_dump_json=lambda: '{"batch_id":"batch"}',
    )

    provider_run_id = provider.submit(cast(Any, job))
    status = provider.status(provider_run_id)

    assert provider_run_id == "fc-test"
    assert function.job_json == '{"batch_id":"batch"}'
    assert status.state == OcrProviderState.RUNNING


def test_modal_provider_downloads_only_the_committed_protocol_files(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    completed_call = _FakeCall(
        {
            "batch_id": "batch",
            "output_prefix": "runs/batch",
            "state": "complete",
        }
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
    monkeypatch.setattr(
        modal,
        "Volume",
        type(
            "Volume",
            (),
            {"from_name": staticmethod(lambda *_args, **_kwargs: _FakeVolume())},
        ),
    )
    destination = tmp_path / "output"

    _provider().download_output("fc-test", destination)

    assert (destination / "result_manifest.json").read_bytes() == (
        b"runs/batch/result_manifest.json"
    )
    assert (destination / "result.tar.zst").read_bytes() == b"runs/batch/result.tar.zst"


def test_modal_provider_maps_an_invalid_remote_result_to_failed(monkeypatch: Any) -> None:
    call = _FakeCall({"state": "complete"})
    provider = _provider()

    def find_call(_provider_run_id: str) -> Any:
        return call

    monkeypatch.setattr(provider, "_call", find_call)

    status = provider.status("fc-test")

    assert status.state == OcrProviderState.FAILED
    assert status.failure_message == "Modal OCR returned an invalid result"
