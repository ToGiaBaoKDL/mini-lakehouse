from datetime import datetime, timedelta
from typing import Any

from prefect import flow, task

from mini_lakehouse.config import get_settings
from mini_lakehouse.sources.github_archive.models import ArchiveHour
from mini_lakehouse.sources.github_archive.service import GithubArchiveIngestionService
from orchestration.plugins.notifications import (
    notify_flow_failure,
    notify_flow_running,
    notify_flow_success,
    notify_task_failure,
)
from orchestration.utils.dbt import run_dbt_pipeline
from orchestration.utils.retries import (
    DBT_RETRY_DELAY_SECONDS,
    INGESTION_RETRY_DELAYS_SECONDS,
    retry_transient_ingestion_error,
)

DBT_SELECTOR = "engineering_pipeline"
FRESHNESS_SELECTOR = "github_archive_freshness"


@task(
    name="etl_ingest_github_archive_hour",
    retries=len(INGESTION_RETRY_DELAYS_SECONDS),
    retry_delay_seconds=INGESTION_RETRY_DELAYS_SECONDS,
    retry_condition_fn=retry_transient_ingestion_error,
    on_failure=[notify_task_failure],
)
def ingest_archive_hour(target_hour: datetime | str | None = None) -> dict[str, Any]:
    result = GithubArchiveIngestionService(get_settings()).ingest(ArchiveHour.parse(target_hour))
    return result.model_dump(mode="json")


@task(
    name="etl_build_github_dbt_models",
    retries=1,
    retry_delay_seconds=DBT_RETRY_DELAY_SECONDS,
    on_failure=[notify_task_failure],
)
def build_github_models(*, validate_freshness: bool) -> None:
    run_dbt_pipeline(
        DBT_SELECTOR,
        freshness_selector=FRESHNESS_SELECTOR if validate_freshness else None,
    )


@flow(
    name="etl_github_archive",
    log_prints=True,
    on_running=[notify_flow_running],
    on_completion=[notify_flow_success],
    on_failure=[notify_flow_failure],
    on_cancellation=[notify_flow_failure],
    on_crashed=[notify_flow_failure],
)
def etl_github_archive(
    start_hour: datetime | str | None = None,
    end_hour: datetime | str | None = None,
) -> dict[str, Any]:
    is_scheduled = start_hour is None
    window_start, window_end = ArchiveHour.resolve_window(
        start_hour,
        end_hour,
    )
    current = window_start.value
    ingestion_results: list[dict[str, Any]] = []
    while current <= window_end.value:
        ingestion_results.append(ingest_archive_hour(current))
        current += timedelta(hours=1)

    # Historical custom runs must not fail because their source hour is intentionally old.
    build_github_models(validate_freshness=is_scheduled)
    return {
        "mode": "scheduled" if is_scheduled else "backfill",
        "start_hour": window_start.value.isoformat().replace("+00:00", "Z"),
        "end_hour": window_end.value.isoformat().replace("+00:00", "Z"),
        "hours_processed": len(ingestion_results),
        "ingestion_results": ingestion_results,
    }


if __name__ == "__main__":
    etl_github_archive()
