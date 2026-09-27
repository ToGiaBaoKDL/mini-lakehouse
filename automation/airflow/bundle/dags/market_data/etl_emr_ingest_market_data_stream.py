"""Certify, publish, and audit one terminal SSI Stream trading day."""

from datetime import timedelta

from airflow.sdk import DAG, CronPartitionTimetable
from callbacks.notifications import (
    dag_failure_callbacks,
    dag_success_callbacks,
    task_data_quality_callbacks,
)
from config.assets import CURATED_MARKET_DATA, CURATED_T0_TRADING, T0_PROMOTION_EVIDENCE
from config.templates import (
    DAG_START_DATE,
    LOCAL_TIMEZONE,
    partition_key_or_run_date,
    runtime_value,
)
from operators.docker import docker_task
from operators.emr import emr_spark_job

TRADE_DATE = partition_key_or_run_date()

with DAG(
    dag_id="etl_emr_ingest_market_data_stream",
    description="Certify and publish one SSI Stream trade-date partition.",
    schedule=CronPartitionTimetable(
        "0 17 * * 1-5",
        timezone=LOCAL_TIMEZONE,
        run_immediately=False,
        key_format="%Y-%m-%d",
    ),
    start_date=DAG_START_DATE,
    catchup=False,
    max_active_runs=1,
    on_failure_callback=dag_failure_callbacks(),
    on_success_callback=dag_success_callbacks(),
    tags=["market-data", "etl", "emr", "ssi", "stream", "iceberg"],
) as dag:
    validate = docker_task(
        task_id="validate_market_data_stream",
        image="t0-trading:runtime",
        command=[
            "validate-stream-day",
            "--trade-date",
            TRADE_DATE,
            "--landing-uri",
            runtime_value("storage/landing_uri"),
        ],
        workload="t0-trading",
        execution_timeout=timedelta(minutes=30),
        cpus=1,
        mem_limit="1g",
        skip_on_exit_code=99,
    )
    publish = emr_spark_job(
        task_id="publish_market_data_stream",
        job_name=f"ssi-market-data-stream-{TRADE_DATE}",
        entry_point="entrypoints/market_data_stream.py",
        entry_point_arguments=[
            "--source-date",
            TRADE_DATE,
            "--landing-uri",
            runtime_value("storage/landing_uri"),
            "--trading-config-uri",
            f"{runtime_value('emr/code_uri')}/trading.yaml",
        ],
        outlets=[CURATED_MARKET_DATA, CURATED_T0_TRADING],
        spark_conf={
            "spark.driver.cores": "2",
            "spark.driver.memory": "4g",
            "spark.executor.cores": "2",
            "spark.executor.memory": "4g",
            "spark.dynamicAllocation.maxExecutors": "2",
        },
    )
    certify = docker_task(
        task_id="certify_market_data_stream",
        image="t0-trading:runtime",
        command=[
            "certify-stream-day",
            "--trade-date",
            TRADE_DATE,
            "--landing-uri",
            runtime_value("storage/landing_uri"),
        ],
        workload="t0-trading",
        execution_timeout=timedelta(minutes=30),
        cpus=1,
        mem_limit="1g",
        skip_on_exit_code=10,
        on_skipped_callback=task_data_quality_callbacks(
            detail="Stream evidence was published, but deterministic features were withheld."
        ),
    )
    publish_promotion = docker_task(
        task_id="publish_t0_promotion_evidence",
        image="t0-trading:runtime",
        command=[
            "publish-promotion-evidence",
            "--trade-date",
            TRADE_DATE,
            "--landing-uri",
            runtime_value("storage/landing_uri"),
        ],
        workload="t0-trading",
        execution_timeout=timedelta(minutes=45),
        cpus=2,
        mem_limit="2g",
        retries=1,
        retry_delay=timedelta(minutes=10),
        skip_on_exit_code=99,
        outlets=[T0_PROMOTION_EVIDENCE],
    )
    validate.set_downstream(publish)
    publish.set_downstream(certify)
    certify.set_downstream(publish_promotion)
