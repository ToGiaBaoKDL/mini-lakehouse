"""Manual replay of one terminal SSI Stream session on EMR Serverless."""

from airflow.sdk import DAG, Param
from airflow.sdk.definitions.param import ParamsDict
from callbacks.notifications import dag_failure_callbacks, dag_success_callbacks
from config.assets import CURATED_MARKET_DATA
from config.templates import DAG_START_DATE
from operators.emr import emr_spark_job

CAPTURE_MANIFEST = "{{ params.capture_manifest_uri or '' }}"

with DAG(
    dag_id="etl_emr_ingest_market_data_stream",
    description="Replay one verified terminal SSI Stream capture into Iceberg tables.",
    schedule=None,
    start_date=DAG_START_DATE,
    catchup=False,
    max_active_runs=1,
    on_failure_callback=dag_failure_callbacks(),
    on_success_callback=dag_success_callbacks(),
    params=ParamsDict(
        {
            "capture_manifest_uri": Param(
                default=None,
                type=["null", "string"],
                description="Terminal manifest URI emitted by a completed stream capture.",
            )
        }
    ),
    tags=["market-data", "etl", "emr", "ssi", "stream", "iceberg"],
) as dag:
    emr_spark_job(
        task_id="replay_stream_session",
        job_name="ssi-market-data-stream-replay",
        entry_point="entrypoints/market_data_stream.py",
        entry_point_arguments=[
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
