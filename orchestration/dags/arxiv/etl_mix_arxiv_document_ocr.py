"""Manually OCR one curated ArXiv PDF through Kaggle or Modal."""

from datetime import timedelta

import pendulum
from airflow.sdk import DAG, Param, task
from airflow.sdk.definitions.param import ParamsDict
from airflow.sdk.exceptions import AirflowFailException

from orchestration.callbacks.notifications import (
    dag_failure_callbacks,
    dag_success_callbacks,
    task_failure_callbacks,
)


@task(
    task_id="process_arxiv_pdf",
    retries=2,
    retry_delay=timedelta(minutes=5),
    execution_timeout=timedelta(hours=5),
    on_failure_callback=task_failure_callbacks(),
)
def process_arxiv_pdf(arxiv_id: str, provider_name: str) -> dict[str, object]:
    from document_ocr.application import TerminalOcrError

    from orchestration.runtime.ocr import run_arxiv_ocr

    try:
        return run_arxiv_ocr(arxiv_id, provider_name)
    except (TerminalOcrError, ValueError) as error:
        raise AirflowFailException(str(error)) from error


with DAG(
    dag_id="etl_mix_arxiv_document_ocr",
    description="Run and publish OCR for exactly one curated ArXiv PDF.",
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="Asia/Ho_Chi_Minh"),
    catchup=False,
    max_active_runs=1,
    on_failure_callback=dag_failure_callbacks(),
    on_success_callback=dag_success_callbacks(),
    params=ParamsDict(
        {
            "arxiv_id": Param(
                default=None,
                type=["null", "string"],
                maxLength=255,
                description="One exact ArXiv ID. This DAG never selects additional documents.",
            ),
            "provider": Param(
                default="kaggle",
                type="string",
                enum=["kaggle", "modal"],
                description="Remote GPU execution provider.",
            ),
        }
    ),
    tags=["arxiv", "etl", "ocr", "gpu"],
) as dag:
    process_arxiv_pdf(
        arxiv_id="{{ params.arxiv_id or '' }}",
        provider_name="{{ params.provider }}",
    )
