"""Manually extract one curated ArXiv PDF through a configured pipeline."""

from datetime import timedelta

from airflow.sdk import DAG, Param
from airflow.sdk.definitions.param import ParamsDict
from callbacks.notifications import (
    dag_failure_callbacks,
    dag_success_callbacks,
)
from config.assets import CURATED_ARXIV_METADATA, CURATED_ARXIV_OCR
from config.templates import DAG_START_DATE
from operators.docker import docker_task

OCR_IMAGE = "ocr-worker:runtime"

with DAG(
    dag_id="etl_docker_arxiv_document_ocr",
    description="Run and publish document extraction for exactly one curated ArXiv PDF.",
    schedule=None,
    start_date=DAG_START_DATE,
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
            "pipeline": Param(
                default="opendataloader_cpu",
                type="string",
                enum=["opendataloader_cpu", "glm_ocr_modal"],
                description="YAML-configured processor and execution backend.",
            ),
        }
    ),
    tags=["arxiv", "etl", "docker", "document-extraction"],
) as dag:
    docker_task(
        task_id="process_arxiv_pdf",
        image=OCR_IMAGE,
        command=[
            "--arxiv-id",
            "{{ params.arxiv_id }}",
            "--pipeline",
            "{{ params.pipeline }}",
        ],
        workload="ocr-worker",
        cpus=2.0,
        mem_limit="6g",
        execution_timeout=timedelta(hours=5),
        inlets=[CURATED_ARXIV_METADATA],
        outlets=[CURATED_ARXIV_OCR],
    )
