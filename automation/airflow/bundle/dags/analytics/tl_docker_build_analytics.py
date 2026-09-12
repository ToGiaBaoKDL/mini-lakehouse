"""Build every analytics domain on one predictable daily schedule."""

from datetime import timedelta

from airflow.sdk import DAG, TaskGroup
from callbacks.notifications import (
    dag_failure_callbacks,
    dag_success_callbacks,
)
from config.templates import DAG_START_DATE, runtime_value
from operators.docker import docker_task


def _analytics_group(
    domain: str,
    *,
    check_freshness: bool = True,
) -> TaskGroup:
    environment = {
        "DBT_ANALYTICS_URI": runtime_value("storage/analytics_uri"),
        "DBT_DOMAIN": domain,
        "DBT_QUERY_RESULTS_URI": runtime_value(f"athena/dbt_{domain}_output_uri"),
        "DBT_SCHEMA": f"analytics_{domain}",
    }
    with TaskGroup(group_id=domain) as group:
        build = docker_task(
            task_id="build_analytics",
            image="dbt:runtime",
            command=["build", "--selector", domain],
            workload=f"dbt-{domain}",
            execution_timeout=timedelta(hours=2),
            environment=environment,
        )
        if check_freshness:
            freshness = docker_task(
                task_id="check_source_freshness",
                image="dbt:runtime",
                command=["source", "freshness", "--selector", domain],
                workload=f"dbt-{domain}",
                execution_timeout=timedelta(minutes=30),
                environment=environment,
            )
            freshness.set_downstream(build)
    return group


with DAG(
    dag_id="tl_docker_build_analytics",
    description="Build every analytics domain daily at 12:30 Asia/Ho_Chi_Minh.",
    schedule="30 12 * * *",
    start_date=DAG_START_DATE,
    catchup=False,
    max_active_runs=1,
    on_failure_callback=dag_failure_callbacks(),
    on_success_callback=dag_success_callbacks(),
    tags=["analytics", "tl", "dbt", "docker"],
) as dag:
    _analytics_group("engineering")
    _analytics_group("research")
    _analytics_group(
        "trading",
        check_freshness=False,
    )
