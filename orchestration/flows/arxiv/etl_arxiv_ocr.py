from typing import Any

from prefect import flow, task

from mini_lakehouse.config import get_settings
from mini_lakehouse.curated_products.arxiv.ocr_service import ArxivOcrService
from orchestration.plugins.notifications import (
    notify_flow_failure,
    notify_flow_running,
    notify_flow_success,
    notify_task_failure,
)


@task(
    name="etl_reconcile_and_submit_arxiv_ocr",
    task_run_name="arxiv-ocr-reconcile-submit",
    retries=1,
    retry_delay_seconds=60,
    on_failure=[notify_task_failure],
)
def reconcile_and_submit_ocr(
    arxiv_ids: tuple[str, ...],
) -> dict[str, Any]:
    result = ArxivOcrService(get_settings()).run_once(
        arxiv_ids=arxiv_ids,
    )
    return result.model_dump(mode="json")


@flow(
    name="etl_arxiv_ocr",
    log_prints=True,
    on_running=[notify_flow_running],
    on_completion=[notify_flow_success],
    on_failure=[notify_flow_failure],
    on_cancellation=[notify_flow_failure],
    on_crashed=[notify_flow_failure],
)
def etl_arxiv_ocr(
    arxiv_ids: list[str] | None = None,
) -> dict[str, Any]:
    identifiers = tuple(dict.fromkeys(arxiv_ids or ()))
    return reconcile_and_submit_ocr(identifiers)


if __name__ == "__main__":
    etl_arxiv_ocr()
