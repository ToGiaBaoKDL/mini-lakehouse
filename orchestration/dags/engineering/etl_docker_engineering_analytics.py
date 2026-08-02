"""Build engineering analytics after the curated GitHub product is published."""

from datetime import timedelta

import pendulum
from airflow.sdk import DAG

from orchestration.callbacks.notifications import (
    dag_failure_callbacks,
    dag_success_callbacks,
)
from orchestration.config.assets import ANALYTICS_ENGINEERING, CURATED_GITHUB
from orchestration.config.templates import runtime_value
from orchestration.operators.docker import docker_task

DBT_IMAGE = "dbt-task:runtime"

DBT_ENVIRONMENT = {
    "DBT_ANALYTICS_URI": runtime_value("storage/analytics_uri"),
    "DBT_QUERY_RESULTS_URI": runtime_value("athena/dbt_output_uri"),
}


with DAG(
    dag_id="etl_docker_engineering_analytics",
    description="Validate curated freshness and build engineering analytics with dbt.",
    schedule=[CURATED_GITHUB],
    start_date=pendulum.datetime(2026, 1, 1, tz="Asia/Ho_Chi_Minh"),
    catchup=False,
    max_active_runs=1,
    on_failure_callback=dag_failure_callbacks(),
    on_success_callback=dag_success_callbacks(),
    tags=["engineering", "analytics", "dbt", "docker"],
) as dag:
    freshness = docker_task(
        task_id="check_source_freshness",
        image=DBT_IMAGE,
        command=["source", "freshness"],
        workload="dbt-transformer",
        execution_timeout=timedelta(hours=2),
        environment=DBT_ENVIRONMENT,
        inlets=[CURATED_GITHUB],
    )
    build = docker_task(
        task_id="build_analytics",
        image=DBT_IMAGE,
        command=["build"],
        workload="dbt-transformer",
        execution_timeout=timedelta(hours=2),
        environment=DBT_ENVIRONMENT,
        inlets=[CURATED_GITHUB],
        outlets=[ANALYTICS_ENGINEERING],
    )

    freshness.set_downstream(build)
