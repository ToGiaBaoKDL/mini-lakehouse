from datetime import datetime, timedelta
from typing import Any

from prefect import flow, task
from prefect_dbt import PrefectDbtRunner, PrefectDbtSettings
from requests import ConnectionError as RequestsConnectionError
from requests import HTTPError, Timeout

from mini_lakehouse.config import get_settings
from mini_lakehouse.github_archive.client import ArchiveNotPublishedError
from mini_lakehouse.github_archive.models import ArchiveHour
from mini_lakehouse.github_archive.service import GithubArchiveIngestionService


def retry_transient_ingestion_error(_task: Any, _task_run: Any, state: Any) -> bool:
    try:
        state.result()
    except ArchiveNotPublishedError:
        return False
    except (RequestsConnectionError, Timeout):
        return True
    except HTTPError as error:
        return error.response is None or error.response.status_code in (429, 500, 502, 503, 504)
    except Exception:
        return False
    return False


@task(
    name="etl_ingest_github_archive_backfill_hour",
    retries=3,
    retry_delay_seconds=[30, 120, 300],
    retry_condition_fn=retry_transient_ingestion_error,
)
def ingest_archive_hour(target_hour: datetime) -> None:
    settings = get_settings()
    GithubArchiveIngestionService(settings).ingest(ArchiveHour(value=target_hour))


@task(name="etl_build_github_backfill_dbt_models", retries=1, retry_delay_seconds=30)
def build_github_models() -> None:
    settings = get_settings()
    runner = PrefectDbtRunner(
        settings=PrefectDbtSettings(
            project_dir=settings.dbt.project_dir,
            profiles_dir=settings.dbt.profiles_dir,
        )
    )
    runner.invoke(["build", "--select", "+tag:mart"])


@flow(name="etl_github_archive_backfill", log_prints=True)
def etl_github_archive_backfill(start: datetime | str, end: datetime | str) -> int:
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
