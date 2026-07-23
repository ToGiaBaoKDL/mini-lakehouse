from datetime import date
from typing import Any

from prefect import flow, task

from mini_lakehouse.config import get_settings
from mini_lakehouse.curated_products.arxiv.service import ArxivCurationService
from mini_lakehouse.sources.arxiv.models import OaiDay
from mini_lakehouse.sources.arxiv.service import ArxivMetadataService
from orchestration.plugins.notifications import (
    notify_flow_failure,
    notify_flow_running,
    notify_flow_success,
    notify_task_failure,
)
from orchestration.utils.retries import (
    CURATION_RETRY_DELAY_SECONDS,
    INGESTION_RETRY_DELAYS_SECONDS,
    retry_transient_ingestion_error,
)


@task(
    name="etl_sync_arxiv_metadata_day",
    task_run_name="sync-arxiv-metadata-{datestamp_date}",
    retries=len(INGESTION_RETRY_DELAYS_SECONDS),
    retry_delay_seconds=INGESTION_RETRY_DELAYS_SECONDS,
    retry_condition_fn=retry_transient_ingestion_error,
    on_failure=[notify_task_failure],
)
def sync_metadata_day(
    datestamp_date: date,
    refresh: bool,
) -> dict[str, Any]:
    result = ArxivMetadataService(get_settings()).sync_day(
        OaiDay(value=datestamp_date),
        refresh=refresh,
    )
    return result.model_dump(mode="json")


@task(
    name="etl_curate_arxiv_metadata_day",
    task_run_name="curate-arxiv-metadata-{datestamp_date}",
    retries=1,
    retry_delay_seconds=CURATION_RETRY_DELAY_SECONDS,
    on_failure=[notify_task_failure],
)
def curate_metadata_day(datestamp_date: date) -> dict[str, Any]:
    result = ArxivCurationService(get_settings()).curate_day(datestamp_date)
    return result.model_dump(mode="json")


@flow(
    name="etl_arxiv_metadata",
    log_prints=True,
    on_running=[notify_flow_running],
    on_completion=[notify_flow_success],
    on_failure=[notify_flow_failure],
    on_cancellation=[notify_flow_failure],
    on_crashed=[notify_flow_failure],
)
def etl_arxiv_metadata(
    datestamp_date: date | str | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    day = OaiDay.parse(datestamp_date)
    metadata_result = sync_metadata_day(day.value, refresh)
    curation_result = curate_metadata_day(day.value)
    return {
        "datestamp_date": day.iso,
        "metadata_result": metadata_result,
        "curation_result": curation_result,
    }


if __name__ == "__main__":
    etl_arxiv_metadata()
