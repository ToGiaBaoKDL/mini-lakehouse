from typing import Any

from prefect import flow, task

from mini_lakehouse.config import get_settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.processing.ocr.provider import OcrProviderName
from mini_lakehouse.processing.ocr.providers import create_ocr_provider
from orchestration.plugins.notifications import (
    notify_flow_failure,
    notify_flow_running,
    notify_flow_success,
    notify_task_failure,
)


@task(
    name="gov_reconcile_arxiv_ocr_resource",
    task_run_name="arxiv-ocr-{provider}-resources-reconcile",
    retries=2,
    retry_delay_seconds=60,
    on_failure=[notify_task_failure],
)
def reconcile_arxiv_ocr_resources(provider: OcrProviderName) -> dict[str, Any]:
    settings = get_settings()
    processor = load_contracts(settings.contracts_dir).processor("arxiv_glm_ocr")
    return create_ocr_provider(settings, processor, provider).reconcile_resources()


@flow(
    name="gov_arxiv_ocr_resources",
    log_prints=True,
    on_running=[notify_flow_running],
    on_completion=[notify_flow_success],
    on_failure=[notify_flow_failure],
    on_cancellation=[notify_flow_failure],
    on_crashed=[notify_flow_failure],
)
def gov_arxiv_ocr_resources(
    provider: OcrProviderName | None = None,
) -> dict[str, Any]:
    """Reconcile the selected provider's immutable OCR runtime resources."""
    settings = get_settings()
    processor = load_contracts(settings.contracts_dir).processor("arxiv_glm_ocr")
    selected = provider or processor.runner.default_provider
    return reconcile_arxiv_ocr_resources(selected)


if __name__ == "__main__":
    gov_arxiv_ocr_resources()
