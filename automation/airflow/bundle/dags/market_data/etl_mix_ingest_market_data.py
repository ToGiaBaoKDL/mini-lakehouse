"""Scheduled bounded SSI REST capture and lakehouse publication."""

from datetime import timedelta

from airflow.sdk import DAG, Param
from airflow.sdk.definitions.param import ParamsDict
from callbacks.notifications import dag_failure_callbacks, dag_success_callbacks
from config.assets import CURATED_MARKET_DATA
from config.templates import DAG_START_DATE, LOCAL_TIMEZONE, runtime_value
from operators.docker import docker_task
from operators.emr import emr_spark_job

RUN_LOCAL_DATE = (
    "{{ dag_run.run_after.astimezone(macros.dateutil.tz.gettz('"
    f"{LOCAL_TIMEZONE}')).strftime('%Y-%m-%d') }}}}"
)
TRADE_DATE = "{{ ti.xcom_pull(task_ids='resolve_trade_date') }}"
CAPTURE_MANIFEST = "{{ ti.xcom_pull(task_ids='capture_rest') }}"

with DAG(
    dag_id="etl_mix_ingest_market_data",
    description="Capture one SSI trade date and publish reconciled market-data Iceberg tables.",
    schedule="30 1 * * 2-6",
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
                description="Completed exchange-local day; defaults to the latest SSI trading day.",
            )
        }
    ),
    tags=["market-data", "etl", "mix", "ssi", "iceberg"],
) as dag:
    resolve = docker_task(
        task_id="resolve_trade_date",
        image="t0-trading:runtime",
        command=[
            "resolve-trade-date",
            "--before-date",
            RUN_LOCAL_DATE,
            "--trade-date",
            "{{ params.trade_date or '' }}",
        ],
        workload="t0-trading",
        execution_timeout=timedelta(minutes=5),
        cpus=0.25,
        mem_limit="256m",
    )
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
    resolve.set_downstream(capture)
    capture.set_downstream(publish)
