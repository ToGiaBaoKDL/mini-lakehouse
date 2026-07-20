from datetime import datetime
from typing import Any

from prefect import task
from prefect_dbt import PrefectDbtRunner, PrefectDbtSettings
from requests import HTTPError

from mini_lakehouse.config import get_settings
from mini_lakehouse.github_archive.client import ArchiveNotPublishedError
from mini_lakehouse.github_archive.models import ArchiveHour
from mini_lakehouse.github_archive.service import GithubArchiveIngestionService


def retry_transient_ingestion_error(_task: Any, _task_run: Any, state: Any) -> bool:
    try:
        state.result()
    except ArchiveNotPublishedError:
        return False
    except HTTPError as error:
        return error.response is None or error.response.status_code in (429, 500, 502, 503, 504)
    except Exception:
        return True
    return False


@task(
    name="ingest-github-archive-hour",
    retries=3,
    retry_delay_seconds=[30, 120, 300],
    retry_condition_fn=retry_transient_ingestion_error,
)
def ingest_archive_hour(target_hour: datetime | str | None = None) -> dict[str, Any]:
    settings = get_settings()
    result = GithubArchiveIngestionService(settings).ingest(ArchiveHour.parse(target_hour))
    return result.model_dump(mode="json")


@task(name="build-github-dbt-models", retries=1, retry_delay_seconds=30)
def build_github_models() -> None:
    settings = get_settings()
    dbt_settings = PrefectDbtSettings(
        project_dir=settings.dbt.project_dir,
        profiles_dir=settings.dbt.profiles_dir,
    )
    runner = PrefectDbtRunner(settings=dbt_settings)
    runner.invoke(["source", "freshness"])
    runner.invoke(["build", "--select", "+tag:mart"])
