"""Scheduled replay of terminal SSI Stream sessions on EMR Serverless."""

from airflow.sdk import DAG, CronPartitionTimetable
from callbacks.notifications import dag_failure_callbacks, dag_success_callbacks
from config.assets import CURATED_MARKET_DATA
from config.templates import (
    DAG_START_DATE,
    LOCAL_TIMEZONE,
    partition_key_or_run_date,
    runtime_value,
)
from operators.emr import emr_spark_job

TRADE_DATE = partition_key_or_run_date()

with DAG(
    dag_id="etl_emr_ingest_market_data_stream",
    description="Replay one SSI Stream trade-date partition into Iceberg tables.",
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
    tags=["market-data", "etl", "emr", "ssi", "stream", "iceberg"],
) as dag:
    emr_spark_job(
        task_id="replay_stream_session",
        job_name=f"ssi-market-data-stream-{TRADE_DATE}",
        entry_point="entrypoints/market_data_stream.py",
        entry_point_arguments=[
            "--source-date",
            TRADE_DATE,
            "--landing-uri",
            runtime_value("storage/landing_uri"),
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
