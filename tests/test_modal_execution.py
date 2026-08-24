import json
from pathlib import Path
from typing import Any, cast

import modal
import pytest
from document_ocr.config import GlmOcrConfig, load_arxiv_config
from document_ocr.execution import ExecutionError, ModalCredentials, ModalExecution
from document_ocr.protocol import GlmOcrJob
from pydantic import ValidationError


class _FakeCall:
    object_id = "fc-test"

    def __init__(self, result: object | None = None, *, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.cancelled = False
        self.logs = _FakeLogs()

    def get(self, timeout: float | None) -> object:
        if self._error is not None:
            raise self._error
        return self._result

    def get_dashboard_url(self) -> str:
        return "https://modal.com/id/fc-test"

    def cancel(self, terminate_containers: bool = False) -> None:
        self.cancelled = terminate_containers


class _FakeLogEntry:
    message = "worker started\n"


class _FakeLogs:
    def stream(self):
        yield _FakeLogEntry()


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


def _pipeline() -> GlmOcrConfig:
    pipeline = load_arxiv_config().pipeline("glm_ocr")
    assert isinstance(pipeline, GlmOcrConfig)
    return pipeline


def _execution() -> ModalExecution:
    return ModalExecution(
        ModalCredentials.model_validate({"token_id": "ak-test", "token_secret": "as-test"}),
        _pipeline(),
        client=cast(Any, object()),
    )


def _job() -> GlmOcrJob:
    return GlmOcrJob.model_construct(run_id="run", adapter="glm_ocr")


def test_modal_configuration_is_explicit_and_credentials_require_a_pair() -> None:
    assert _pipeline().modal.environment == "main"
    with pytest.raises(ValidationError, match="token_secret"):
        cast(Any, ModalCredentials)(token_id="ak-incomplete")


def test_modal_execution_submits_waits_and_downloads_committed_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_prefix = f"runs/{'b' * 64}"
    call = _FakeCall(output_prefix)
    function = _FakeFunction(call)
    monkeypatch.setattr(
        modal,
        "Function",
        type("Function", (), {"from_name": staticmethod(lambda *_args, **_kwargs: function)}),
    )
    monkeypatch.setattr(
        modal,
        "FunctionCall",
        type("FunctionCall", (), {"from_id": staticmethod(lambda *_args, **_kwargs: call)}),
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
                            f"{output_prefix}/result.json": b"result",
                            f"{output_prefix}/artifacts.tar.zst": b"archive",
                        }
                    )
                )
            },
        ),
    )
    checkpoints: list[str] = []
    logs: list[str] = []
    destination = tmp_path / "output"

    _execution().execute(
        _job(),
        destination,
        logs.append,
        resume_token=None,
        checkpoint=checkpoints.append,
    )

    assert checkpoints == ["fc-test"]
    assert function.job_json is not None
    assert json.loads(function.job_json) == {
        "schema_version": "3.0.0",
        "run_id": "run",
        "adapter": "glm_ocr",
    }
    assert logs == [
        "Modal function call: https://modal.com/id/fc-test\n",
        "worker started\n",
    ]
    assert (destination / "result.json").read_bytes() == b"result"
    assert (destination / "artifacts.tar.zst").read_bytes() == b"archive"


def test_modal_execution_cancels_its_active_call(monkeypatch: pytest.MonkeyPatch) -> None:
    call = _FakeCall(error=modal.exception.TimeoutError("still running"))
    function = _FakeFunction(call)
    monkeypatch.setattr(
        modal,
        "Function",
        type("Function", (), {"from_name": staticmethod(lambda *_args, **_kwargs: function)}),
    )
    monkeypatch.setattr(
        modal,
        "FunctionCall",
        type("FunctionCall", (), {"from_id": staticmethod(lambda *_args, **_kwargs: call)}),
    )
    execution = _execution()

    with pytest.raises(ExecutionError):
        execution.execute(
            _job(),
            Path("unused"),
            lambda _message: None,
            resume_token=None,
            checkpoint=lambda _token: None,
        )
    execution.cancel()

    assert call.cancelled is True


@pytest.mark.parametrize(
    ("result", "error", "message"),
    [
        ({"state": "complete"}, None, "invalid output prefix"),
        (None, ValueError("invalid worker payload"), "invalid worker payload"),
    ],
)
def test_modal_execution_rejects_invalid_remote_results(
    monkeypatch: pytest.MonkeyPatch,
    result: object,
    error: Exception | None,
    message: str,
) -> None:
    call = _FakeCall(result, error=error)
    execution = _execution()

    def find_call(_run_id: str) -> _FakeCall:
        return call

    monkeypatch.setattr(execution, "_call", find_call)

    with pytest.raises(ExecutionError, match=message):
        execution.execute(
            _job(),
            Path("unused"),
            lambda _message: None,
            resume_token="fc-test",
            checkpoint=lambda _token: None,
        )
