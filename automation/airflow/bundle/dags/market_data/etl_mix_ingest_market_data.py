"""Manual bounded SSI REST capture and lakehouse publication."""

from datetime import timedelta

from airflow.sdk import DAG, Param
from airflow.sdk.definitions.param import ParamsDict
from callbacks.notifications import dag_failure_callbacks, dag_success_callbacks
from config.assets import CURATED_MARKET_DATA
from config.templates import DAG_START_DATE, previous_local_date, runtime_value
from operators.docker import docker_task
from operators.emr import emr_spark_job

TRADE_DATE = previous_local_date("trade_date")
CAPTURE_MANIFEST = "{{ ti.xcom_pull(task_ids='capture_rest') }}"

with DAG(
    dag_id="etl_mix_ingest_market_data",
    description="Capture one SSI trade date and publish reconciled market-data Iceberg tables.",
    schedule=None,
    start_date=DAG_START_DATE,
    catchup=False,
    max_active_runs=1,
    on_failure_callback=dag_failure_callbacks(),
    on_success_callback=dag_success_callbacks(),
    params=ParamsDict(
        {
            "trade_date": Param(
                default=None,
                type=["null", "string"],
                format="date",
                description="Completed exchange-local day; defaults to the previous local date.",
            )
        }
    ),
    tags=["market-data", "etl", "mix", "ssi", "iceberg"],
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
    )
    publish = emr_spark_job(
        task_id="publish_market_data",
        job_name=f"ssi-market-data-{TRADE_DATE}",
        entry_point="entrypoints/market_data.py",
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
