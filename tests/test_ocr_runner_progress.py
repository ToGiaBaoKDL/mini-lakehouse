import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any


def _progress_module() -> ModuleType:
    path = Path("runners/glm_ocr/progress.py")
    spec = importlib.util.spec_from_file_location("glm_ocr_runner_progress", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _PipelineState:
    def __init__(self) -> None:
        self.layout_results_dict = {
            0: [{}, {}],
            1: [{}],
            2: [],
        }
        self.results = {
            0: [{}, {}],
            1: [],
            2: [],
        }

    def get_grouped_results(self, page_indices: list[int]) -> list[list[dict[str, Any]]]:
        return [self.results[index] for index in page_indices]


def test_page_progress_emits_each_completed_page_once() -> None:
    progress = _progress_module()
    state = _PipelineState()
    parser = SimpleNamespace(_pipeline=SimpleNamespace(_current_state=state))
    events: list[tuple[str, dict[str, Any]]] = []

    def capture(event: str, _started_at: float, **fields: Any) -> None:
        events.append((event, fields))

    monitor = progress.GlmOcrPageProgress(
        parser,
        arxiv_id="2607.00001",
        document_count=2,
        document_index=1,
        page_count=3,
        request_id="a" * 64,
        started_at=1.0,
        emit=capture,
    )

    monitor._capture_completed_pages()
    state.results[1].append({})
    monitor._capture_completed_pages()
    monitor._capture_completed_pages()

    assert [fields["page_number"] for _, fields in events] == [1, 3, 2]
    assert [fields["pages_completed"] for _, fields in events] == [1, 2, 3]
    assert events[-1] == (
        "page_completed",
        {
            "arxiv_id": "2607.00001",
            "document_count": 2,
            "document_index": 1,
            "page_count": 3,
            "request_id": "a" * 64,
            "region_count": 1,
            "page_number": 2,
            "pages_completed": 3,
            "progress_percent": 100.0,
        },
    )
