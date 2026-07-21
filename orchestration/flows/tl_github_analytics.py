from datetime import datetime
from typing import Any

from prefect import flow, task

from mini_lakehouse.config import get_settings
from mini_lakehouse.products.github.service import GithubCurationService
from mini_lakehouse.sources.github_archive.models import ArchiveHour
from orchestration.plugins.notifications import (
    notify_flow_failure,
    notify_flow_running,
    notify_flow_success,
    notify_task_failure,
)
from orchestration.utils.dbt import run_dbt_pipeline
from orchestration.utils.retries import (
    CURATION_RETRY_DELAY_SECONDS,
    DBT_RETRY_DELAY_SECONDS,
)
from orchestration.utils.scheduling import resolve_archive_hour


@task(
    name="tl_curate_github_hour",
    task_run_name="curate-github-{source_hour}",
    retries=1,
    retry_delay_seconds=CURATION_RETRY_DELAY_SECONDS,
    on_failure=[notify_task_failure],
)
def curate_github_hour(source_hour: datetime) -> dict[str, Any]:
    result = GithubCurationService(get_settings()).curate(ArchiveHour(value=source_hour))
    return result.model_dump(mode="json")


@task(
    name="tl_build_github_analytics",
    task_run_name="build-github-analytics",
    retries=1,
    retry_delay_seconds=DBT_RETRY_DELAY_SECONDS,
    on_failure=[notify_task_failure],
)
def build_github_analytics() -> None:
    run_dbt_pipeline()


@flow(
    name="tl_github_analytics",
    log_prints=True,
    on_running=[notify_flow_running],
    on_completion=[notify_flow_success],
    on_failure=[notify_flow_failure],
    on_cancellation=[notify_flow_failure],
    on_crashed=[notify_flow_failure],
)
def tl_github_analytics(
    archive_hour: datetime | str | None = None,
) -> dict[str, Any]:
    source_hour = resolve_archive_hour(archive_hour)
    curation_result = curate_github_hour(source_hour.value)
    build_github_analytics()
    return {
        "source_hour": source_hour.value.isoformat().replace("+00:00", "Z"),
        "curation_result": curation_result,
    }


if __name__ == "__main__":
    tl_github_analytics()
