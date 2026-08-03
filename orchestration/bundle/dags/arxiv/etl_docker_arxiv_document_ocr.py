"""Manually OCR one curated ArXiv PDF through Kaggle or Modal."""

from datetime import timedelta

import pendulum
from airflow.sdk import DAG, Param
from airflow.sdk.definitions.param import ParamsDict
from airflow_bundle.callbacks.notifications import (
    dag_failure_callbacks,
    dag_success_callbacks,
)
from airflow_bundle.config.assets import CURATED_ARXIV_METADATA, CURATED_ARXIV_OCR
from airflow_bundle.operators.docker import docker_task

OCR_IMAGE = "ocr-worker:runtime"

with DAG(
    dag_id="etl_docker_arxiv_document_ocr",
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
                type="string",
                minLength=1,
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
    tags=["arxiv", "etl", "docker", "ocr", "gpu"],
) as dag:
    docker_task(
        task_id="process_arxiv_pdf",
        image=OCR_IMAGE,
        command=[
            "--arxiv-id",
            "{{ params.arxiv_id }}",
            "--provider",
            "{{ params.provider }}",
        ],
        workload="ocr-worker",
        execution_timeout=timedelta(hours=5),
        inlets=[CURATED_ARXIV_METADATA],
        outlets=[CURATED_ARXIV_OCR],
    )
