"""Build Research analytics when ArXiv metadata or OCR results are published."""

from datetime import timedelta

from airflow.sdk import DAG
from callbacks.notifications import (
    dag_failure_callbacks,
    dag_success_callbacks,
)
from config.assets import (
    ANALYTICS_RESEARCH,
    CURATED_ARXIV_METADATA,
    CURATED_ARXIV_OCR,
)
from config.templates import DAG_START_DATE, runtime_value
from operators.docker import docker_task

DBT_ENVIRONMENT = {
    "DBT_ANALYTICS_URI": runtime_value("storage/analytics_uri"),
    "DBT_DOMAIN": "research",
    "DBT_QUERY_RESULTS_URI": runtime_value("athena/dbt_research_output_uri"),
    "DBT_SCHEMA": "analytics_research",
}


with DAG(
    dag_id="tl_docker_research_analytics",
    description="Validate ArXiv freshness and build Research analytics.",
    schedule=CURATED_ARXIV_METADATA | CURATED_ARXIV_OCR,
    start_date=DAG_START_DATE,
    catchup=False,
    max_active_runs=1,
    on_failure_callback=dag_failure_callbacks(),
    on_success_callback=dag_success_callbacks(),
    tags=["research", "tl", "dbt", "docker"],
) as dag:
    freshness = docker_task(
        task_id="check_source_freshness",
        image="dbt:runtime",
        command=["source", "freshness", "--selector", "research"],
        workload="dbt-research",
        execution_timeout=timedelta(minutes=30),
        environment=DBT_ENVIRONMENT,
        inlets=[CURATED_ARXIV_METADATA],
    )
    build = docker_task(
        task_id="build_analytics",
        image="dbt:runtime",
        command=["build", "--selector", "research"],
        workload="dbt-research",
        execution_timeout=timedelta(hours=2),
        environment=DBT_ENVIRONMENT,
        inlets=[CURATED_ARXIV_METADATA, CURATED_ARXIV_OCR],
        outlets=[ANALYTICS_RESEARCH],
    )

    freshness.set_downstream(build)
