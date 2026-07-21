"""Shared retry policies for external I/O and compute tasks."""

from typing import Any

from requests import ConnectionError as RequestsConnectionError
from requests import HTTPError, Timeout

from mini_lakehouse.sources.github_archive.client import ArchiveNotPublishedError

INGESTION_RETRY_DELAYS_SECONDS = [30.0, 120.0, 300.0]
DBT_RETRY_DELAY_SECONDS = 30
CURATION_RETRY_DELAY_SECONDS = 30
MAINTENANCE_RETRY_DELAY_SECONDS = 60


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
