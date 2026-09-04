"""Daily GitHub Archive source-to-curated processing on EMR Serverless."""

from datetime import timedelta

from airflow.sdk import DAG, CronPartitionTimetable
from callbacks.notifications import dag_failure_callbacks, dag_success_callbacks
from config.assets import CURATED_GITHUB
from config.templates import (
    DAG_START_DATE,
    LOCAL_TIMEZONE,
    partition_key_or_previous_date,
    runtime_value,
)
from operators.docker import docker_task
from operators.emr import emr_spark_job

SOURCE_DATE = partition_key_or_previous_date()
CAPTURE_MANIFEST = "{{ ti.xcom_pull(task_ids='capture_github_archive') }}"

with DAG(
    dag_id="etl_emr_ingest_github_archive",
    description="Load one UTC GitHub Archive day into landing and curated Iceberg tables.",
    schedule=CronPartitionTimetable(
        "30 7 * * *",
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
    tags=["github", "etl", "emr", "docker", "iceberg"],
) as dag:
    capture = docker_task(
        task_id="capture_github_archive",
        image="lakehouse-ingest:runtime",
        command=[
            "github-archive",
            "--source-date",
            SOURCE_DATE,
            "--landing-uri",
            runtime_value("storage/landing_uri"),
        ],
        workload="lakehouse-ingest",
        execution_timeout=timedelta(minutes=45),
        cpus=2,
        mem_limit="1g",
        retries=1,
        retry_delay=timedelta(minutes=10),
        do_xcom_push=True,
    )
    publish = emr_spark_job(
        task_id="process_github_archive_day",
        job_name=f"github-archive-{SOURCE_DATE}",
        entry_point="entrypoints/github_archive.py",
        entry_point_arguments=[
            "--source-date",
            SOURCE_DATE,
            "--capture-manifest-uri",
            CAPTURE_MANIFEST,
        ],
        outlets=[CURATED_GITHUB],
        spark_conf={
            "spark.driver.cores": "2",
            "spark.driver.memory": "8g",
            "spark.executor.cores": "4",
            "spark.executor.memory": "16g",
            "spark.dynamicAllocation.maxExecutors": "3",
        },
    )
    capture.set_downstream(publish)
