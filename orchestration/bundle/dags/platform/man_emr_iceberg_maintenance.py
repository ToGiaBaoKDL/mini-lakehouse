"""Maintain contract-owned landing and curated Iceberg tables."""

from airflow.sdk import DAG
from airflow_bundle.callbacks.notifications import dag_failure_callbacks, dag_success_callbacks
from airflow_bundle.config.templates import DAG_START_DATE
from airflow_bundle.operators.emr import emr_spark_job

with DAG(
    dag_id="man_emr_iceberg_maintenance",
    description="Compact recent partitions and prune expired Iceberg metadata and files.",
    schedule="0 3 * * 0",
    start_date=DAG_START_DATE,
    catchup=False,
    max_active_runs=1,
    on_failure_callback=dag_failure_callbacks(),
    on_success_callback=dag_success_callbacks(),
    tags=["platform", "maintenance", "man", "emr", "iceberg"],
) as dag:
    emr_spark_job(
        task_id="maintain_contract_tables",
        job_name="iceberg-maintenance-{{ ds_nodash }}",
        entry_point="entrypoints/iceberg_maintenance.py",
        entry_point_arguments=[],
        spark_conf={
            "spark.driver.cores": "2",
            "spark.driver.memory": "8g",
            "spark.executor.cores": "2",
            "spark.executor.memory": "8g",
            "spark.dynamicAllocation.maxExecutors": "2",
        },
    )
