"""Build Engineering analytics when its curated product is published."""

from datetime import timedelta

from airflow.sdk import DAG
from callbacks.notifications import (
    dag_failure_callbacks,
    dag_success_callbacks,
)
from config.assets import ANALYTICS_ENGINEERING, CURATED_GITHUB
from config.templates import DAG_START_DATE, runtime_value
from operators.docker import docker_task

DBT_ENVIRONMENT = {
    "DBT_ANALYTICS_URI": runtime_value("storage/analytics_uri"),
    "DBT_QUERY_RESULTS_URI": runtime_value("athena/dbt_engineering_output_uri"),
}


with DAG(
    dag_id="tl_docker_engineering_analytics",
    description="Validate GitHub freshness and build Engineering analytics.",
    schedule=CURATED_GITHUB,
    start_date=DAG_START_DATE,
    catchup=False,
    max_active_runs=1,
    on_failure_callback=dag_failure_callbacks(),
    on_success_callback=dag_success_callbacks(),
    tags=["engineering", "tl", "dbt", "docker"],
) as dag:
    freshness = docker_task(
        task_id="check_source_freshness",
        image="dbt-engineering:runtime",
        command=["source", "freshness"],
        workload="dbt-engineering",
        execution_timeout=timedelta(minutes=30),
        environment=DBT_ENVIRONMENT,
        inlets=[CURATED_GITHUB],
    )
    build = docker_task(
        task_id="build_analytics",
        image="dbt-engineering:runtime",
        command=["build"],
        workload="dbt-engineering",
        execution_timeout=timedelta(hours=2),
        environment=DBT_ENVIRONMENT,
        inlets=[CURATED_GITHUB],
        outlets=[ANALYTICS_ENGINEERING],
    )

    freshness.set_downstream(build)
