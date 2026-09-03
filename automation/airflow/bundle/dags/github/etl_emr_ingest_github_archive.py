"""Daily GitHub Archive source-to-curated processing on EMR Serverless."""

from airflow.sdk import DAG
from callbacks.notifications import dag_failure_callbacks, dag_success_callbacks
from config.assets import CURATED_GITHUB
from config.templates import DAG_START_DATE, data_interval_start_date, runtime_value
from operators.emr import emr_spark_job

SOURCE_DATE = data_interval_start_date()

with DAG(
    dag_id="etl_emr_ingest_github_archive",
    description="Load one UTC GitHub Archive day into landing and curated Iceberg tables.",
    schedule="30 7 * * *",
    start_date=DAG_START_DATE,
    catchup=False,
    max_active_runs=1,
    on_failure_callback=dag_failure_callbacks(),
    on_success_callback=dag_success_callbacks(),
    tags=["github", "etl", "emr", "iceberg"],
) as dag:
    emr_spark_job(
        task_id="process_github_archive_day",
        job_name=f"github-archive-{SOURCE_DATE}",
        entry_point="entrypoints/github_archive.py",
        entry_point_arguments=[
            "--source-date",
            SOURCE_DATE,
            "--landing-uri",
            runtime_value("storage/landing_uri"),
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
