"""HTTP client configured with standard urllib3 retry semantics."""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def http_session() -> requests.Session:
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers["User-Agent"] = "lakehouse/0.3"
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session
