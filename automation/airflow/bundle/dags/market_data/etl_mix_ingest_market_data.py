"""Capture and publish one complete SSI market-data trade-date partition."""

from datetime import timedelta

from airflow.sdk import DAG, CronPartitionTimetable, chain
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
REST_CAPTURE_MANIFEST = "{{ ti.xcom_pull(task_ids='capture_market_data_rest') }}"

with DAG(
    dag_id="etl_mix_ingest_market_data",
    description="Capture, publish, certify, and audit one SSI market-data trade date.",
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
    tags=["market-data", "etl", "mix", "ssi", "rest", "stream", "iceberg"],
) as dag:
    capture_rest = docker_task(
        task_id="capture_market_data_rest",
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
    publish_rest = emr_spark_job(
        task_id="publish_market_data_rest",
        job_name=f"ssi-market-data-rest-{TRADE_DATE}",
        entry_point="entrypoints/market_data_rest.py",
        entry_point_arguments=[
            "--source-date",
            TRADE_DATE,
            "--capture-manifest-uri",
            REST_CAPTURE_MANIFEST,
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
    validate_stream = docker_task(
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
    publish_stream = emr_spark_job(
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
    certify_stream = docker_task(
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
    ensure_shadow_journal = docker_task(
        task_id="ensure_t0_shadow_journal",
        image="t0-trading:runtime",
        command=[
            "ensure-shadow-journal",
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

    chain(
        capture_rest,
        publish_rest,
        validate_stream,
        publish_stream,
        certify_stream,
        ensure_shadow_journal,
        publish_promotion,
    )
