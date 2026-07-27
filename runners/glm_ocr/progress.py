"""Structured page progress for the provider-neutral GLM-OCR pipeline."""

import threading
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self

ProgressEmitter = Callable[..., None]


class GlmOcrPageProgress:
    """Observe completed pages without changing the GLM-OCR parse pipeline."""

    def __init__(
        self,
        parser: Any,
        *,
        arxiv_id: str,
        document_count: int,
        document_index: int,
        page_count: int,
        request_id: str,
        started_at: float,
        emit: ProgressEmitter,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self._parser = parser
        self._fields = {
            "arxiv_id": arxiv_id,
            "document_count": document_count,
            "document_index": document_index,
            "page_count": page_count,
            "request_id": request_id,
        }
        self._page_count = page_count
        self._started_at = started_at
        self._emit = emit
        self._poll_interval_seconds = poll_interval_seconds
        self._completed: set[int] = set()
        self._monitor_failed = False
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._monitor,
            name=f"ocr-progress-{request_id[:12]}",
            daemon=True,
        )

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self._poll_interval_seconds * 2))
        self._capture_completed_pages()
        if exception_type is None:
            for page_index in range(self._page_count):
                self._report_page(page_index)

    def _monitor(self) -> None:
        while not self._stop.wait(self._poll_interval_seconds):
            self._capture_completed_pages()

    def _capture_completed_pages(self) -> None:
        if self._monitor_failed:
            return
        try:
            self._capture_pipeline_state()
        except Exception as error:
            self._monitor_failed = True
            self._emit(
                "page_progress_unavailable",
                self._started_at,
                **self._fields,
                error_message=str(error)[:500],
                error_type=type(error).__name__,
            )

    def _capture_pipeline_state(self) -> None:
        # GLM-OCR 0.1.5 has no public page callback. This adapter observes its
        # pinned pipeline state while leaving parsing and result ordering intact.
        pipeline = getattr(self._parser, "_pipeline", None)
        state = getattr(pipeline, "_current_state", None)
        layout_results = getattr(state, "layout_results_dict", None)
        grouped_results = getattr(state, "get_grouped_results", None)
        if not isinstance(layout_results, dict) or not callable(grouped_results):
            return

        page_indices = sorted(
            page_index
            for page_index in list(layout_results)
            if isinstance(page_index, int) and 0 <= page_index < self._page_count
        )
        if not page_indices:
            return
        completed_regions = grouped_results(page_indices)
        for page_index, regions in zip(page_indices, completed_regions, strict=True):
            expected = layout_results.get(page_index)
            if isinstance(expected, list) and len(regions) >= len(expected):
                self._report_page(page_index, region_count=len(expected))

    def _report_page(self, page_index: int, *, region_count: int | None = None) -> None:
        if page_index in self._completed:
            return
        self._completed.add(page_index)
        pages_completed = len(self._completed)
        region_fields = {"region_count": region_count} if region_count is not None else {}
        self._emit(
            "page_completed",
            self._started_at,
            **self._fields,
            **region_fields,
            page_number=page_index + 1,
            pages_completed=pages_completed,
            progress_percent=round(pages_completed * 100 / self._page_count, 1),
        )
