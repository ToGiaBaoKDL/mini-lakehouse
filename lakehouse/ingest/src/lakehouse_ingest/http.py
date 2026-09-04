"""HTTP session with bounded retries for idempotent source reads."""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def session() -> requests.Session:
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
    )
    result = requests.Session()
    result.headers["User-Agent"] = "lakehouse-ingest/0.1"
    result.mount("https://", HTTPAdapter(max_retries=retry))
    return result
