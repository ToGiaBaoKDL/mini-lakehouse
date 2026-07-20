from datetime import UTC, datetime, timedelta
from typing import Any

from prefect import flow

from mini_lakehouse.github_archive.models import ArchiveHour
from mini_lakehouse.orchestration.tasks import build_github_models, ingest_archive_hour


@flow(name="github-archive-hourly", log_prints=True)
def ingest_and_transform_github_archive(
    target_hour: datetime | str | None = None,
) -> dict[str, Any]:
    ingestion_result = ingest_archive_hour(target_hour)
    build_github_models()
    return ingestion_result


@flow(name="github-archive-backfill", log_prints=True)
def backfill_github_archive(start: datetime | str, end: datetime | str) -> int:
    start_hour = ArchiveHour.parse(start)
    end_hour = ArchiveHour.parse(end)
    if start_hour.value > end_hour.value:
        raise ValueError("start must be less than or equal to end")

    current = start_hour.value
    ingested_hours = 0
    while current <= end_hour.value:
        ingest_archive_hour(current)
        ingested_hours += 1
        current += timedelta(hours=1)
    build_github_models()
    return ingested_hours


if __name__ == "__main__":
    ingest_and_transform_github_archive(ArchiveHour.previous_complete_hour(datetime.now(UTC)).value)
