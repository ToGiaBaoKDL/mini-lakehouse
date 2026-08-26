"""Build only the analytics domains affected by curated asset events."""

from collections.abc import Mapping, Sequence
from datetime import timedelta

from airflow.models.asset import AssetEvent
from airflow.models.dagrun import DagRun
from airflow.providers.standard.operators.python import ShortCircuitOperator
from airflow.sdk import DAG, Asset, TaskGroup
from airflow.utils.types import DagRunType
from callbacks.notifications import (
    dag_failure_callbacks,
    dag_success_callbacks,
)
from config.assets import (
    ANALYTICS_ENGINEERING,
    ANALYTICS_RESEARCH,
    CURATED_ARXIV_METADATA,
    CURATED_GITHUB,
)
from config.templates import DAG_START_DATE, runtime_value
from operators.docker import docker_task


def _domain_was_triggered(
    *,
    asset_uris: tuple[str, ...],
    dag_run: DagRun,
    triggering_asset_events: Mapping[Asset, Sequence[AssetEvent]] | None = None,
) -> bool:
    """Run every domain manually, or only domains touched by an asset-triggered run."""
    if dag_run.run_type == DagRunType.MANUAL:
        return True
    return any(
        asset.uri in asset_uris and events
        for asset, events in (triggering_asset_events or {}).items()
    )


def _analytics_group(
    domain: str,
    *,
    inputs: tuple[Asset, ...],
    output: Asset,
) -> TaskGroup:
    environment = {
        "DBT_ANALYTICS_URI": runtime_value("storage/analytics_uri"),
        "DBT_DOMAIN": domain,
        "DBT_QUERY_RESULTS_URI": runtime_value(f"athena/dbt_{domain}_output_uri"),
        "DBT_SCHEMA": f"analytics_{domain}",
    }
    with TaskGroup(group_id=domain) as group:
        selected = ShortCircuitOperator(
            task_id="should_run",
            python_callable=_domain_was_triggered,
            op_kwargs={"asset_uris": tuple(asset.uri for asset in inputs)},
        )
        freshness = docker_task(
            task_id="check_source_freshness",
            image="dbt:runtime",
            command=["source", "freshness", "--selector", domain],
            workload=f"dbt-{domain}",
            execution_timeout=timedelta(minutes=30),
            environment=environment,
            inlets=inputs,
        )
        build = docker_task(
            task_id="build_analytics",
            image="dbt:runtime",
            command=["build", "--selector", domain],
            workload=f"dbt-{domain}",
            execution_timeout=timedelta(hours=2),
            environment=environment,
            inlets=inputs,
            outlets=[output],
        )
        selected.set_downstream(freshness)
        freshness.set_downstream(build)
    return group


with DAG(
    dag_id="tl_docker_build_analytics",
    description="Build only analytics domains affected by curated asset events.",
    schedule=CURATED_GITHUB | CURATED_ARXIV_METADATA,
    start_date=DAG_START_DATE,
    catchup=False,
    max_active_runs=1,
    on_failure_callback=dag_failure_callbacks(),
    on_success_callback=dag_success_callbacks(),
    tags=["analytics", "tl", "dbt", "docker"],
) as dag:
    _analytics_group(
        "engineering",
        inputs=(CURATED_GITHUB,),
        output=ANALYTICS_ENGINEERING,
    )
    _analytics_group(
        "research",
        inputs=(CURATED_ARXIV_METADATA,),
        output=ANALYTICS_RESEARCH,
    )
