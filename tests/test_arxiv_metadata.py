import hashlib
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from mini_lakehouse.config.settings import ArxivSettings, Settings
from mini_lakehouse.sources.arxiv.archive import write_response_archive
from mini_lakehouse.sources.arxiv.client import ArxivOaiClient
from mini_lakehouse.sources.arxiv.models import OaiDay
from mini_lakehouse.sources.arxiv.parser import parse_oai_pages
from mini_lakehouse.sources.arxiv.repository import (
    ArxivLandingDayState,
    ArxivLandingWrite,
)
from mini_lakehouse.sources.arxiv.service import ArxivMetadataService

OAI_PAGE = b"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"
         xmlns:arXiv="http://arxiv.org/OAI/arXiv/">
  <responseDate>2026-07-23T00:00:00Z</responseDate>
  <request verb="ListRecords">https://oaipmh.arxiv.org/oai</request>
  <ListRecords>
    <record>
      <header>
        <identifier>oai:arXiv.org:2607.00001</identifier>
        <datestamp>2026-07-22</datestamp>
        <setSpec>cs:cs.AI</setSpec>
      </header>
      <metadata>
        <arXiv:arXiv>
          <arXiv:id>2607.00001</arXiv:id>
          <arXiv:created>2026-07-20</arXiv:created>
          <arXiv:updated>2026-07-22</arXiv:updated>
          <arXiv:authors>
            <arXiv:author>
              <arXiv:keyname>Nguyen</arXiv:keyname>
              <arXiv:forenames>An</arXiv:forenames>
            </arXiv:author>
          </arXiv:authors>
          <arXiv:title>  A   test paper  </arXiv:title>
          <arXiv:abstract>Test abstract.</arXiv:abstract>
          <arXiv:categories>cs.AI cs.LG</arXiv:categories>
        </arXiv:arXiv>
      </metadata>
    </record>
    <resumptionToken></resumptionToken>
  </ListRecords>
</OAI-PMH>
"""


class _Response:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        pass


class _Session:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = responses
        self.calls: list[dict[str, str]] = []

    def get(self, _url: str, *, params: dict[str, str], timeout: float) -> _Response:
        assert timeout == 120
        self.calls.append(params)
        return _Response(self.responses.pop(0))


def test_oai_page_is_the_lineage_source_for_typed_landing_records() -> None:
    table = parse_oai_pages(
        (OAI_PAGE,),
        OaiDay(value=date(2026, 7, 22)),
        raw_object_key="api/arxiv/raw/oai/datestamp=2026-07-22/responses.tar.zst",
        raw_object_sha256="a" * 64,
        ingested_at=datetime(2026, 7, 23, tzinfo=UTC),
    )

    assert table.num_rows == 1
    row: dict[str, Any] = table.to_pylist()[0]
    assert row["arxiv_id"] == "2607.00001"
    assert row["title"] == "A test paper"
    assert row["categories"] == "cs.AI cs.LG"
    assert row["primary_category"] == "cs.AI"
    assert row["pdf_url"] == "https://arxiv.org/pdf/2607.00001"
    assert row["raw_object_key"].endswith("responses.tar.zst")
    assert row["raw_object_sha256"] == "a" * 64
    assert len(row["record_sha256"]) == 64


def test_oai_client_uses_a_closed_inclusive_day_and_follows_resumption_token() -> None:
    first = OAI_PAGE.replace(
        b"<resumptionToken></resumptionToken>",
        b"<resumptionToken>next-page</resumptionToken>",
    )
    second = OAI_PAGE
    session = _Session([first, second])
    client = ArxivOaiClient(ArxivSettings(), session=session)  # type: ignore[arg-type]

    pages = tuple(client.pages(OaiDay(value=date(2026, 7, 22))))

    assert pages == (first, second)
    assert session.calls == [
        {
            "verb": "ListRecords",
            "metadataPrefix": "arXiv",
            "from": "2026-07-22",
            "until": "2026-07-22",
        },
        {"verb": "ListRecords", "resumptionToken": "next-page"},
    ]


def test_raw_response_archive_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.tar.zst"
    second = tmp_path / "second.tar.zst"

    first_hash = write_response_archive((OAI_PAGE,), first)
    second_hash = write_response_archive((OAI_PAGE,), second)

    assert first.read_bytes() == second.read_bytes()
    assert first_hash == second_hash == hashlib.sha256(first.read_bytes()).hexdigest()


class _UnusedClient:
    def pages(self, _day: OaiDay) -> tuple[bytes, ...]:
        raise AssertionError("A complete checkpoint must not re-harvest OAI")


class _ObjectStore:
    def __init__(self, *, exists: bool, sha256: str = "a" * 64) -> None:
        self.object_exists = exists
        self.object_sha256 = sha256
        self.uploads = 0

    def exists(self, _uri: str) -> bool:
        return self.object_exists

    def sha256(self, _uri: str) -> str:
        return self.object_sha256

    def upload(self, _source: Path, _destination_uri: str) -> None:
        self.uploads += 1


class _Repository:
    def __init__(self, state: ArxivLandingDayState | None) -> None:
        self.state = state
        self.publications = 0

    def day_state(self, _day: date) -> ArxivLandingDayState | None:
        return self.state

    def publish_day(self, *_args: object, **_kwargs: object) -> ArxivLandingWrite:
        self.publications += 1
        return ArxivLandingWrite(
            records_snapshot_id=11,
            checkpoint_snapshot_id=12,
        )


def test_complete_arxiv_checkpoint_is_a_noop_retry() -> None:
    state = ArxivLandingDayState(
        raw_object_key="api/arxiv/raw/oai/datestamp=2026-07-22/responses.tar.zst",
        raw_object_sha256="a" * 64,
        page_count=2,
        record_count=5,
        schema_version="arxiv.oai_records_raw.v1",
        records_snapshot_id=7,
        checkpoint_snapshot_id=8,
    )
    repository = _Repository(state)
    object_store = _ObjectStore(exists=True)
    service = ArxivMetadataService(
        Settings(),
        client=_UnusedClient(),  # type: ignore[arg-type]
        object_store=object_store,  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
    )

    result = service.sync_day(OaiDay(value=date(2026, 7, 22)))

    assert result.was_written is False
    assert result.records_snapshot_id == 7
    assert result.checkpoint_snapshot_id == 8
    assert object_store.uploads == 0
    assert repository.publications == 0


def test_explicit_arxiv_refresh_replaces_the_daily_checkpoint() -> None:
    state = ArxivLandingDayState(
        raw_object_key="api/arxiv/raw/oai/datestamp=2026-07-22/responses.tar.zst",
        raw_object_sha256="a" * 64,
        page_count=1,
        record_count=1,
        schema_version="arxiv.oai_records_raw.v1",
        records_snapshot_id=7,
        checkpoint_snapshot_id=8,
    )
    repository = _Repository(state)
    object_store = _ObjectStore(exists=True)
    service = ArxivMetadataService(
        Settings(),
        client=ArxivOaiClient(ArxivSettings(), session=_Session([OAI_PAGE])),  # type: ignore[arg-type]
        object_store=object_store,  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
    )

    result = service.sync_day(OaiDay(value=date(2026, 7, 22)), refresh=True)

    assert result.was_written is True
    assert object_store.uploads == 1
    assert repository.publications == 1


def test_mismatched_raw_object_rebuilds_the_checkpoint_on_retry() -> None:
    state = ArxivLandingDayState(
        raw_object_key="api/arxiv/raw/oai/datestamp=2026-07-22/responses.tar.zst",
        raw_object_sha256="a" * 64,
        page_count=1,
        record_count=1,
        schema_version="arxiv.oai_records_raw.v1",
        records_snapshot_id=7,
        checkpoint_snapshot_id=8,
    )
    repository = _Repository(state)
    object_store = _ObjectStore(exists=True, sha256="b" * 64)
    service = ArxivMetadataService(
        Settings(),
        client=ArxivOaiClient(ArxivSettings(), session=_Session([OAI_PAGE])),  # type: ignore[arg-type]
        object_store=object_store,  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
    )

    result = service.sync_day(OaiDay(value=date(2026, 7, 22)))

    assert result.was_written is True
    assert object_store.uploads == 1
    assert repository.publications == 1
