from datetime import datetime

from prefect.runtime import flow_run

from mini_lakehouse.sources.github_archive.models import ArchiveHour


def resolve_archive_hour(value: datetime | str | None) -> ArchiveHour:
    if value is not None:
        return ArchiveHour.parse(value)
    return ArchiveHour.previous_complete_hour(flow_run.scheduled_start_time)
