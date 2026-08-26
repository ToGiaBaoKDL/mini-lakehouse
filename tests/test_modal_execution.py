import json
from pathlib import Path
from typing import Any, cast

import modal
import pytest
from document_ocr.config import load_config
from document_ocr.modal import ModalOcr
from document_ocr.protocol import OcrError, OcrJob


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


class _FakeMethod:
    def __init__(self, call: _FakeCall) -> None:
        self.call = call
        self.job_json: str | None = None

    def spawn(self, job_json: str) -> _FakeCall:
        self.job_json = job_json
        return self.call


class _FakeLogs:
    def stream(self) -> tuple[()]:
        return ()


class _FakeVolume:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def read_file(self, path: str) -> tuple[bytes, ...]:
        return (self.objects[path],)


def _job() -> OcrJob:
    return load_config().job(
        document_id="2607.00001",
        source_record_sha256="a" * 64,
        pdf_url="https://arxiv.org/pdf/2607.00001",
        reuse=None,
    )


def _client() -> ModalOcr:
    return ModalOcr(
        token_id="ak-test",
        token_secret="as-test",
        config=load_config(),
        client=cast(Any, object()),
    )


def _install_modal(
    monkeypatch: pytest.MonkeyPatch,
    method: _FakeMethod,
    volume: _FakeVolume,
) -> None:
    class _Remote:
        run = method

    class _RemoteClass:
        def __call__(self) -> _Remote:
            return _Remote()

    remote_class = _RemoteClass()
    monkeypatch.setattr(
        modal,
        "Cls",
        type(
            "Cls",
            (),
            {"from_name": staticmethod(lambda *_args, **_kwargs: remote_class)},
        ),
    )
    monkeypatch.setattr(
        modal,
        "Volume",
        type(
            "Volume",
            (),
            {"from_name": staticmethod(lambda *_args, **_kwargs: volume)},
        ),
    )


def test_modal_run_submits_and_downloads_committed_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prefix = f"jobs/{'b' * 64}"
    method = _FakeMethod(_FakeCall(prefix))
    _install_modal(
        monkeypatch,
        method,
        _FakeVolume(
            {
                f"{prefix}/result.json": b"result",
                f"{prefix}/artifacts.tar.zst": b"archive",
            }
        ),
    )
    job = _job()
    destination = tmp_path / "output"

    _client().run(job, destination)

    assert json.loads(cast(str, method.job_json)) == job.model_dump(mode="json")
    assert (destination / "result.json").read_bytes() == b"result"
    assert (destination / "artifacts.tar.zst").read_bytes() == b"archive"


def test_modal_failure_can_cancel_the_active_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    call = _FakeCall(error=ValueError("invalid worker payload"))
    method = _FakeMethod(call)
    _install_modal(monkeypatch, method, _FakeVolume({}))
    client = _client()

    with pytest.raises(OcrError, match="invalid worker payload"):
        client.run(_job(), tmp_path / "unused")
    client.cancel()

    assert call.cancelled is True


@pytest.mark.parametrize("prefix", ["invalid", "runs/" + "a" * 64])
def test_modal_rejects_invalid_output_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    prefix: str,
) -> None:
    method = _FakeMethod(_FakeCall(prefix))
    _install_modal(monkeypatch, method, _FakeVolume({}))

    with pytest.raises(OcrError, match="invalid output prefix"):
        _client().run(_job(), tmp_path / "unused")
