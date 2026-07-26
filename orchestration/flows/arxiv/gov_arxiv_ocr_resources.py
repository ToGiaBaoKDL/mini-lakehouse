from typing import Any

from prefect import flow, task

from mini_lakehouse.config import get_settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.processing.ocr.kaggle_provider import KaggleProvider
from mini_lakehouse.processing.ocr.kaggle_types import KaggleResourceName
from orchestration.plugins.notifications import (
    notify_flow_failure,
    notify_flow_running,
    notify_flow_success,
    notify_task_failure,
)


@task(
    name="gov_reconcile_arxiv_ocr_resource",
    task_run_name="arxiv-ocr-{resource_name}-reconcile",
    retries=2,
    retry_delay_seconds=60,
    on_failure=[notify_task_failure],
)
def reconcile_arxiv_ocr_resource(resource_name: KaggleResourceName) -> dict[str, Any]:
    settings = get_settings()
    processor = load_contracts(settings.contracts_dir).processor("arxiv_glm_ocr")
    result = KaggleProvider(settings.kaggle, processor).reconcile_resource(resource_name)
    return result.model_dump(mode="json")


@flow(
    name="gov_arxiv_ocr_resources",
    log_prints=True,
    on_running=[notify_flow_running],
    on_completion=[notify_flow_success],
    on_failure=[notify_flow_failure],
    on_cancellation=[notify_flow_failure],
    on_crashed=[notify_flow_failure],
)
def gov_arxiv_ocr_resources() -> dict[str, Any]:
    """Reconcile every immutable Kaggle resource required by the ArXiv OCR runner."""
    return {
        "runner": reconcile_arxiv_ocr_resource("runner"),
        "runtime": reconcile_arxiv_ocr_resource("runtime"),
        "model": reconcile_arxiv_ocr_resource("model"),
        "layout_model": reconcile_arxiv_ocr_resource("layout_model"),
    }


if __name__ == "__main__":
    gov_arxiv_ocr_resources()
