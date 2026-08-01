"""Manually OCR one curated ArXiv PDF through Kaggle or Modal."""

import os
from datetime import timedelta

import pendulum
from airflow.providers.docker.operators.docker import DockerOperator
from airflow.sdk import DAG, Param
from airflow.sdk.definitions.param import ParamsDict
from docker.types import Mount

from orchestration.callbacks.notifications import (
    dag_failure_callbacks,
    dag_success_callbacks,
    task_failure_callbacks,
)

OCR_IMAGE = "ocr-worker:runtime"
OCR_AWS_PROFILE = os.environ["OCR_AWS_PROFILE"]
HOST_AWS_CONFIG_DIR = os.environ["HOST_AWS_CONFIG_DIR"]

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
    tags=["arxiv", "etl", "docker", "ocr", "gpu"],
) as dag:
    DockerOperator(
        task_id="process_arxiv_pdf",
        image=OCR_IMAGE,
        command=[
            "--arxiv-id",
            "{{ params.arxiv_id or '' }}",
            "--provider",
            "{{ params.provider }}",
        ],
        docker_url="unix://var/run/docker.sock",
        environment={
            "AWS_PROFILE": OCR_AWS_PROFILE,
            "LAKEHOUSE_ENVIRONMENT": os.environ["LAKEHOUSE_ENVIRONMENT"],
        },
        mounts=[
            Mount(
                source=HOST_AWS_CONFIG_DIR,
                target="/tmp/.aws",
                type="bind",
                read_only=True,
            )
        ],
        user=f"{os.getuid()}:0",
        force_pull=False,
        auto_remove="force",
        mount_tmp_dir=False,
        execution_timeout=timedelta(hours=5),
        on_failure_callback=task_failure_callbacks(),
    )
