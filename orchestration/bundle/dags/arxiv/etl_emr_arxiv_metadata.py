"""Daily ArXiv metadata source-to-curated processing on EMR Serverless."""

from airflow.sdk import DAG, Param
from airflow.sdk.definitions.param import ParamsDict
from airflow_bundle.callbacks.notifications import dag_failure_callbacks, dag_success_callbacks
from airflow_bundle.config.assets import CURATED_ARXIV_METADATA
from airflow_bundle.config.templates import DAG_START_DATE, previous_local_date, runtime_value
from airflow_bundle.operators.emr import emr_spark_job

SOURCE_DATE = previous_local_date()

with DAG(
    dag_id="etl_emr_arxiv_metadata",
    description="Harvest one ArXiv OAI day into landing and curated Iceberg tables.",
    schedule="0 11 * * *",
    start_date=DAG_START_DATE,
    catchup=False,
    max_active_runs=1,
    on_failure_callback=dag_failure_callbacks(),
    on_success_callback=dag_success_callbacks(),
    params=ParamsDict(
        {
            "source_date": Param(
                default=None,
                type=["null", "string"],
                format="date",
                description="OAI datestamp day; defaults to the previous local calendar day.",
            )
        }
    ),
    tags=["arxiv", "etl", "emr", "iceberg"],
) as dag:
    emr_spark_job(
        task_id="process_arxiv_metadata_day",
        job_name=f"arxiv-metadata-{SOURCE_DATE}",
        entry_point="entrypoints/arxiv_metadata.py",
        entry_point_arguments=[
            "--source-date",
            SOURCE_DATE,
            "--landing-uri",
            runtime_value("storage/landing_uri"),
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
