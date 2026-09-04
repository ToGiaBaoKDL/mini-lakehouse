"""Daily ArXiv metadata source-to-curated processing on EMR Serverless."""

from datetime import timedelta

from airflow.sdk import DAG, CronPartitionTimetable
from callbacks.notifications import dag_failure_callbacks, dag_success_callbacks
from config.assets import CURATED_ARXIV_METADATA
from config.templates import (
    DAG_START_DATE,
    LOCAL_TIMEZONE,
    partition_key_or_previous_date,
    runtime_value,
)
from operators.docker import docker_task
from operators.emr import emr_spark_job

SOURCE_DATE = partition_key_or_previous_date()
CAPTURE_MANIFEST = "{{ ti.xcom_pull(task_ids='capture_arxiv_oai') }}"

with DAG(
    dag_id="etl_emr_ingest_arxiv_metadata",
    description="Harvest one ArXiv OAI day into landing and curated Iceberg tables.",
    schedule=CronPartitionTimetable(
        "0 11 * * *",
        timezone=LOCAL_TIMEZONE,
        run_offset=-1,
        run_immediately=False,
        key_format="%Y-%m-%d",
    ),
    start_date=DAG_START_DATE,
    catchup=False,
    max_active_runs=1,
    on_failure_callback=dag_failure_callbacks(),
    on_success_callback=dag_success_callbacks(),
    tags=["arxiv", "etl", "emr", "docker", "iceberg"],
) as dag:
    capture = docker_task(
        task_id="capture_arxiv_oai",
        image="lakehouse-ingest:runtime",
        command=[
            "arxiv-oai",
            "--source-date",
            SOURCE_DATE,
            "--landing-uri",
            runtime_value("storage/landing_uri"),
        ],
        workload="lakehouse-ingest",
        execution_timeout=timedelta(minutes=30),
        cpus=1,
        mem_limit="512m",
        retries=1,
        retry_delay=timedelta(minutes=10),
        do_xcom_push=True,
    )
    publish = emr_spark_job(
        task_id="process_arxiv_metadata_day",
        job_name=f"arxiv-metadata-{SOURCE_DATE}",
        entry_point="entrypoints/arxiv_metadata.py",
        entry_point_arguments=[
            "--source-date",
            SOURCE_DATE,
            "--capture-manifest-uri",
            CAPTURE_MANIFEST,
        ],
        outlets=[CURATED_ARXIV_METADATA],
        spark_conf={
            "spark.driver.cores": "2",
            "spark.driver.memory": "8g",
            "spark.executor.cores": "2",
            "spark.executor.memory": "8g",
            "spark.dynamicAllocation.maxExecutors": "2",
        },
    )
    capture.set_downstream(publish)
