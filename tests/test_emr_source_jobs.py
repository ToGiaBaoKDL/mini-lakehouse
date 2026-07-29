import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from lakehouse_jobs.arxiv.oai import parse_records
from lakehouse_jobs.github_archive.extract import has_checksum


def test_github_archive_reuses_only_nonempty_objects_with_sha256_metadata() -> None:
    valid = {
        "ContentLength": 42,
        "Metadata": {"sha256": hashlib.sha256(b"archive").hexdigest()},
    }

    assert has_checksum(valid)
    assert not has_checksum({"ContentLength": 0, "Metadata": valid["Metadata"]})
    assert not has_checksum({"ContentLength": 42, "Metadata": {}})
    assert not has_checksum(None)


def test_arxiv_parser_preserves_regular_records_and_deletion_tombstones() -> None:
    page = b"""\
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListRecords>
    <record>
      <header>
        <identifier>oai:arXiv.org:2401.00001</identifier>
        <datestamp>2026-07-28</datestamp>
        <setSpec>cs:cs.LG</setSpec>
      </header>
      <metadata>
        <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
          <id>2401.00001</id>
          <created>2024-01-01</created>
          <title> Example   paper </title>
          <abstract> Example abstract </abstract>
          <categories>cs.LG cs.AI</categories>
          <authors>
            <author><keyname>Doe</keyname><forenames>Jane</forenames></author>
          </authors>
        </arXiv>
      </metadata>
    </record>
    <record>
      <header status="deleted">
        <identifier>oai:arXiv.org:2401.00002</identifier>
        <datestamp>2026-07-28</datestamp>
      </header>
    </record>
  </ListRecords>
</OAI-PMH>
"""
    ingested_at = datetime(2026, 7, 29, tzinfo=UTC)

    records = parse_records(
        [page],
        source_day=date(2026, 7, 28),
        page_objects=[("api/arxiv/oai/page.xml.gz", "abc123")],
        ingested_at=ingested_at,
    )

    assert [record["arxiv_id"] for record in records] == ["2401.00001", "2401.00002"]
    assert records[0]["title"] == "Example paper"
    assert records[0]["primary_category"] == "cs.LG"
    assert records[0]["is_deleted"] is False
    assert records[1]["is_deleted"] is True
    assert records[1]["title"] is None
    assert records[1]["pdf_url"] == "https://arxiv.org/pdf/2401.00002"


def test_arxiv_parser_rejects_duplicate_oai_identifiers() -> None:
    page = b"""\
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <ListRecords>
    <record>
      <header>
        <identifier>oai:arXiv.org:2401.00001</identifier>
        <datestamp>2026-07-28</datestamp>
      </header>
    </record>
  </ListRecords>
</OAI-PMH>
"""

    with pytest.raises(RuntimeError, match="duplicate identifier"):
        parse_records(
            [page, page],
            source_day=date(2026, 7, 28),
            page_objects=[("page-1.xml.gz", "abc"), ("page-2.xml.gz", "def")],
            ingested_at=datetime(2026, 7, 29, tzinfo=UTC),
        )


def test_github_landing_replay_uses_explicit_day_overwrite() -> None:
    source = Path("jobs/emr/src/lakehouse_jobs/github_archive/job.py").read_text()

    assert ".overwritePartitions()" not in source
    assert ".writeTo(landing_table).overwrite(" in source
    assert 'F.col("source_hour")' in source
