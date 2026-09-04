"""Scheduled bounded SSI REST capture and lakehouse publication."""

from datetime import timedelta

from airflow.sdk import DAG, CronPartitionTimetable
from callbacks.notifications import dag_failure_callbacks, dag_success_callbacks
from config.assets import CURATED_MARKET_DATA
from config.templates import (
    DAG_START_DATE,
    LOCAL_TIMEZONE,
    partition_key_or_run_date,
    runtime_value,
)
from operators.docker import docker_task
from operators.emr import emr_spark_job

TRADE_DATE = partition_key_or_run_date()
CAPTURE_MANIFEST = "{{ ti.xcom_pull(task_ids='capture_rest') }}"

with DAG(
    dag_id="etl_mix_ingest_market_data_rest",
    description="Capture one SSI REST trade date and publish reconciled Iceberg tables.",
    schedule=CronPartitionTimetable(
        "0 21 * * 1-5",
        timezone=LOCAL_TIMEZONE,
        run_immediately=False,
        key_format="%Y-%m-%d",
    ),
    start_date=DAG_START_DATE,
    catchup=False,
    max_active_runs=1,
    on_failure_callback=dag_failure_callbacks(),
    on_success_callback=dag_success_callbacks(),
    tags=["market-data", "etl", "mix", "ssi", "rest", "iceberg"],
) as dag:
    capture = docker_task(
        task_id="capture_rest",
        image="t0-trading:runtime",
        command=[
            "capture-rest",
            "--trade-date",
            TRADE_DATE,
            "--job-token",
            "{{ run_id }}",
            "--landing-uri",
            runtime_value("storage/landing_uri"),
        ],
        workload="t0-trading",
        execution_timeout=timedelta(minutes=30),
        cpus=1,
        mem_limit="1g",
        retries=1,
        retry_delay=timedelta(minutes=10),
        do_xcom_push=True,
        skip_on_exit_code=99,
    )
    publish = emr_spark_job(
        task_id="publish_market_data_rest",
        job_name=f"ssi-market-data-rest-{TRADE_DATE}",
        entry_point="entrypoints/market_data_rest.py",
        entry_point_arguments=[
            "--source-date",
            TRADE_DATE,
            "--capture-manifest-uri",
            CAPTURE_MANIFEST,
        ],
        outlets=[CURATED_MARKET_DATA],
        spark_conf={
            "spark.driver.cores": "2",
            "spark.driver.memory": "4g",
            "spark.executor.cores": "2",
            "spark.executor.memory": "4g",
            "spark.dynamicAllocation.maxExecutors": "2",
        },
    )
    capture.set_downstream(publish)
