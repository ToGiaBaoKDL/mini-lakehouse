"""Bounded OAI-PMH client for one closed ArXiv datestamp day."""

from collections.abc import Iterator
from xml.etree import ElementTree

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from mini_lakehouse.config.settings import ArxivSettings
from mini_lakehouse.sources.arxiv.models import OaiDay

_OAI = "http://www.openarchives.org/OAI/2.0/"


class OaiProtocolError(RuntimeError):
    """An OAI-PMH response is syntactically valid but rejects the request."""


class ArxivOaiClient:
    def __init__(
        self,
        settings: ArxivSettings,
        session: requests.Session | None = None,
    ) -> None:
        self._settings = settings
        self._session = session or self._create_session(settings)

    @staticmethod
    def _create_session(settings: ArxivSettings) -> requests.Session:
        retry = Retry(
            total=4,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        session = requests.Session()
        session.headers["User-Agent"] = settings.user_agent
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session

    def pages(self, day: OaiDay) -> Iterator[bytes]:
        token: str | None = None
        seen_tokens: set[str] = set()
        for page_index in range(self._settings.max_pages_per_day):
            if token is None:
                parameters = {
                    "verb": "ListRecords",
                    "metadataPrefix": self._settings.metadata_prefix,
                    "from": day.iso,
                    "until": day.iso,
                }
            else:
                parameters = {"verb": "ListRecords", "resumptionToken": token}

            response = self._session.get(
                self._settings.base_url,
                params=parameters,
                timeout=self._settings.request_timeout_seconds,
            )
            response.raise_for_status()
            content = response.content
            root = ElementTree.fromstring(content)
            errors = root.findall(f"{{{_OAI}}}error")
            if errors:
                code = errors[0].attrib.get("code", "unknown")
                message = (errors[0].text or "").strip()
                if code == "noRecordsMatch":
                    yield content
                    return
                raise OaiProtocolError(f"ArXiv OAI error {code}: {message}")

            yield content
            token_element = root.find(f"{{{_OAI}}}ListRecords/{{{_OAI}}}resumptionToken")
            token = (token_element.text or "").strip() if token_element is not None else ""
            if not token:
                return
            if token in seen_tokens:
                raise OaiProtocolError(f"ArXiv repeated resumptionToken on page {page_index + 1}")
            seen_tokens.add(token)
        raise OaiProtocolError(
            f"ArXiv exceeded max_pages_per_day={self._settings.max_pages_per_day}"
        )
