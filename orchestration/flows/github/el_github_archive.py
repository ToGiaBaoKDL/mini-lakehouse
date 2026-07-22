from datetime import datetime
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
from orchestration.utils.retries import (
    INGESTION_RETRY_DELAYS_SECONDS,
    retry_transient_ingestion_error,
)
from orchestration.utils.scheduling import resolve_archive_hour


@task(
    name="el_ingest_github_archive_hour",
    task_run_name="ingest-github-archive-{source_hour}",
    retries=len(INGESTION_RETRY_DELAYS_SECONDS),
    retry_delay_seconds=INGESTION_RETRY_DELAYS_SECONDS,
    retry_condition_fn=retry_transient_ingestion_error,
    on_failure=[notify_task_failure],
)
def ingest_archive_hour(source_hour: datetime) -> dict[str, Any]:
    result = GithubArchiveIngestionService(get_settings()).ingest(ArchiveHour(value=source_hour))
    return result.model_dump(mode="json")


@flow(
    name="el_github_archive",
    log_prints=True,
    on_running=[notify_flow_running],
    on_completion=[notify_flow_success],
    on_failure=[notify_flow_failure],
    on_cancellation=[notify_flow_failure],
    on_crashed=[notify_flow_failure],
)
def el_github_archive(archive_hour: datetime | str | None = None) -> dict[str, Any]:
    source_hour = resolve_archive_hour(archive_hour)
    ingestion_result = ingest_archive_hour(source_hour.value)
    return {
        "source_hour": source_hour.value.isoformat().replace("+00:00", "Z"),
        "ingestion_result": ingestion_result,
    }


if __name__ == "__main__":
    el_github_archive()
