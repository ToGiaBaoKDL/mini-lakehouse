"""HTTP client for immutable GitHub Archive source files."""

from collections.abc import Iterator
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from mini_lakehouse.config.settings import GithubArchiveSettings
from mini_lakehouse.sources.github_archive.models import ArchiveHour


class ArchiveNotPublishedError(RuntimeError):
    """Raised when GitHub Archive has not published a requested hour."""


class GithubArchiveClient:
    def __init__(self, settings: GithubArchiveSettings) -> None:
        self._settings = settings
        retry = Retry(
            total=4,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        self._session = requests.Session()
        self._session.headers["User-Agent"] = settings.user_agent
        self._session.mount("https://", HTTPAdapter(max_retries=retry))

    def _chunks(self, archive_hour: ArchiveHour) -> Iterator[bytes]:
        url = f"{self._settings.base_url.rstrip('/')}/{archive_hour.filename}"
        with self._session.get(
            url,
            stream=True,
            timeout=self._settings.request_timeout_seconds,
        ) as response:
            if response.status_code == 404:
                raise ArchiveNotPublishedError(f"Archive hour is not published yet: {url}")
            response.raise_for_status()
            yield from response.iter_content(chunk_size=1024 * 1024)

    def download(self, archive_hour: ArchiveHour, destination: Path) -> Path:
        with destination.open("wb") as output:
            for chunk in self._chunks(archive_hour):
                if chunk:
                    output.write(chunk)
        return destination
