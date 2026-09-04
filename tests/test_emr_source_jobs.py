import gzip
import hashlib
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError
from emr_jobs.arxiv import manifest as arxiv_manifest
from emr_jobs.arxiv.oai import parse_records
from lakehouse.contracts.captures import (
    ArxivOaiManifest,
    ArxivOaiPage,
    GitHubArchiveManifest,
    GitHubArchiveObject,
)
from lakehouse_ingest.arxiv import capture_day as capture_arxiv_day
from lakehouse_ingest.github_archive import is_captured, require_available
from lakehouse_ingest.storage import S3CaptureStore
from pydantic import ValidationError


def test_github_archive_reuses_only_nonempty_objects_with_sha256_metadata() -> None:
    valid = {
        "ContentLength": 42,
        "Metadata": {"sha256": hashlib.sha256(b"archive").hexdigest()},
    }

    assert is_captured(valid)
    assert not is_captured({"ContentLength": 0, "Metadata": valid["Metadata"]})
    assert not is_captured({"ContentLength": 42, "Metadata": {}})
    assert not is_captured(None)


def test_github_archive_checks_latest_missing_hour_first() -> None:
    response = Mock(status_code=404)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)
    http = Mock()
    http.__enter__ = Mock(return_value=http)
    http.__exit__ = Mock(return_value=None)
    http.head.return_value = response

    with (
        patch("lakehouse_ingest.github_archive.session", return_value=http),
        pytest.raises(RuntimeError, match="hour 23 is not published"),
    ):
        require_available(date(2026, 9, 4), [2, 17, 23])

    http.head.assert_called_once_with(
        "https://data.gharchive.org/2026-09-04-23.json.gz",
        allow_redirects=True,
        timeout=(10, 30),
    )


def test_completed_github_archive_retry_makes_no_external_request() -> None:
    with patch("lakehouse_ingest.github_archive.session") as make_session:
        require_available(date(2026, 9, 4), [])

    make_session.assert_not_called()


@pytest.mark.parametrize("existing, conflicts", [(b"manifest", False), (b"other", True)])
def test_terminal_manifest_is_idempotent_and_first_writer_wins(
    existing: bytes,
    conflicts: bool,
) -> None:
    digest = hashlib.sha256(existing).hexdigest()
    client = Mock()
    client.put_object.side_effect = ClientError(
        {"Error": {"Code": "PreconditionFailed"}},
        "PutObject",
    )
    client.head_object.return_value = {
        "ContentLength": len(existing),
        "Metadata": {"sha256": digest},
    }
    client.get_object.return_value = {"Body": BytesIO(existing)}
    store = S3CaptureStore(client, "s3://landing")

    if conflicts:
        with pytest.raises(RuntimeError, match="Immutable capture manifest conflict"):
            store.put_manifest("source/manifest.json", b"manifest")
    else:
        assert store.put_manifest("source/manifest.json", b"manifest") == (
            "s3://landing/source/manifest.json"
        )


def test_capture_manifests_require_complete_ordered_units() -> None:
    captured_at = datetime(2026, 9, 5, tzinfo=UTC)
    duplicate_hours = tuple(
        GitHubArchiveObject(
            hour=0,
            key="api/github_archive/raw/object.json.gz",
            size_bytes=1,
            sha256="a" * 64,
            last_modified=captured_at,
        )
        for _ in range(24)
    )
    with pytest.raises(ValidationError, match="ordered hours 00 through 23"):
        GitHubArchiveManifest(
            source_date=date(2026, 9, 4),
            objects=duplicate_hours,
        )

    with pytest.raises(ValidationError, match="ordered from one"):
        ArxivOaiManifest(
            source_date=date(2026, 9, 4),
            snapshot=hashlib.sha256(("b" * 64).encode()).hexdigest(),
            pages=(
                ArxivOaiPage(
                    page=2,
                    key="api/arxiv/raw/oai/page.xml.gz",
                    size_bytes=1,
                    sha256="b" * 64,
                ),
            ),
        )


def test_emr_runtime_has_no_external_http_client() -> None:
    project = Path("lakehouse/emr/pyproject.toml").read_text()
    source = Path("lakehouse/emr/src")

    assert '"requests' not in project
    assert '"urllib3' not in project
    assert not (source / "emr_jobs/common/http.py").exists()


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
          <categories>cs.LG cs.AI cs.LG</categories>
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
    assert records[0]["categories"] == "cs.LG cs.AI"
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
    source = Path("lakehouse/emr/src/emr_jobs/github_archive/job.py").read_text()

    assert ".overwritePartitions()" not in source
    assert ".writeTo(landing_table).overwrite(" in source
    assert 'F.col("source_hour")' in source


def test_github_landing_parses_once_and_releases_its_cache() -> None:
    landing = Path("lakehouse/emr/src/emr_jobs/github_archive/landing.py").read_text()
    job = Path("lakehouse/emr/src/emr_jobs/github_archive/job.py").read_text()

    assert landing.count("F.from_json(") == 1
    assert landing.count('F.get_json_object("value"') == 1
    assert "landing.count()" not in job
    assert "landing.unpersist()" in job


def test_github_landing_deduplicates_only_identical_event_records() -> None:
    source = Path("lakehouse/emr/src/emr_jobs/github_archive/landing.py").read_text()

    assert 'events.join(duplicate_keys, "event_id", "left_semi")' in source
    assert "duplicate_events.dropDuplicates().cache()" in source
    assert 'F.col("count") > 1' in source
    assert "has {conflict[0]['count']} distinct records" in source
    assert 'events.join(duplicate_keys, "event_id", "left_anti")' in source
    assert ".unionByName(distinct_duplicate_events)" in source


def test_github_current_snapshots_use_a_stable_complete_winner_key() -> None:
    source = Path("lakehouse/emr/src/emr_jobs/github_archive/curated.py").read_text()

    assert source.count("struct(occurred_at, event_id)") == 2
    assert source.count("source.last_observed_at, source.last_event_id") == 2
    assert source.count("target.last_observed_at, target.last_event_id") == 2
    assert source.count("last_event_id = source.last_event_id") == 2
    assert "UPDATE SET *" not in source
    assert "INSERT *" not in source


def test_arxiv_child_replacement_is_unique_and_retry_safe() -> None:
    source = Path("lakehouse/emr/src/emr_jobs/arxiv/curated.py").read_text()
    delete_authors = source.index("MERGE INTO {authors}")
    append_authors = source.index(".writeTo(authors).append()")
    delete_categories = source.index("MERGE INTO {categories}")
    append_categories = source.index(".writeTo(categories).append()")
    merge_papers = source.index("MERGE INTO {papers}")

    assert delete_authors < append_authors < merge_papers
    assert delete_categories < append_categories < merge_papers
    assert "SELECT DISTINCT" in source[source.index(".writeTo(authors)") : append_categories]
    assert "UPDATE SET *" not in source
    assert "INSERT *" not in source


def test_arxiv_capture_uses_immutable_content_snapshots() -> None:
    source = Path("lakehouse/ingest/src/lakehouse_ingest/arxiv.py").read_text()
    emr = Path("lakehouse/emr/src/emr_jobs/arxiv/job.py").read_text()

    assert "snapshot=" in source
    assert "delete" not in source
    assert "http" not in emr
    assert "harvest" not in emr


def test_arxiv_capture_reuses_the_terminal_manifest_without_harvesting() -> None:
    source_day = date(2026, 9, 4)
    compressed = gzip.compress(b"<OAI-PMH />", mtime=0)
    digest = hashlib.sha256(compressed).hexdigest()
    snapshot = hashlib.sha256(digest.encode()).hexdigest()
    key = f"api/arxiv/raw/oai/datestamp={source_day}/snapshot={snapshot}/page-000001.xml.gz"
    manifest = ArxivOaiManifest(
        source_date=source_day,
        snapshot=snapshot,
        pages=(ArxivOaiPage(page=1, key=key, size_bytes=len(compressed), sha256=digest),),
    )
    store = Mock()
    store.read_manifest.return_value = manifest.model_dump_json().encode()
    store.head.return_value = {
        "ContentLength": len(compressed),
        "Metadata": {"sha256": digest},
    }
    expected_uri = f"s3://landing/api/arxiv/raw/oai/datestamp={source_day}/manifest.json"
    store.uri.return_value = expected_uri

    with patch("lakehouse_ingest.arxiv._harvest") as harvest:
        actual_uri = capture_arxiv_day(store, source_day)

    assert actual_uri == expected_uri
    harvest.assert_not_called()
    store.put_file.assert_not_called()
    store.put_manifest.assert_not_called()


def test_arxiv_emr_consumer_validates_the_stable_terminal_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_day = date(2026, 9, 4)
    captured_at = datetime(2026, 9, 5, tzinfo=UTC)
    compressed = gzip.compress(b"<OAI-PMH />", mtime=0)
    page_digest = hashlib.sha256(compressed).hexdigest()
    snapshot = hashlib.sha256(page_digest.encode()).hexdigest()
    logical_page_key = (
        f"api/arxiv/raw/oai/datestamp={source_day}/snapshot={snapshot}/page-000001.xml.gz"
    )
    manifest = ArxivOaiManifest(
        source_date=source_day,
        snapshot=snapshot,
        pages=(
            ArxivOaiPage(
                page=1,
                key=logical_page_key,
                size_bytes=len(compressed),
                sha256=page_digest,
            ),
        ),
    )
    body = manifest.model_dump_json().encode()
    manifest_key = f"root/api/arxiv/raw/oai/datestamp={source_day}/manifest.json"
    physical_page_key = f"root/{logical_page_key}"

    def read_bytes(uri: str) -> bytes:
        return body if uri.endswith("/manifest.json") else compressed

    def head_object(bucket: str, key: str):
        assert bucket == "landing"
        if key == manifest_key:
            return {
                "ContentLength": len(body),
                "Metadata": {"sha256": hashlib.sha256(body).hexdigest()},
                "LastModified": captured_at,
            }
        assert key == physical_page_key
        return {
            "ContentLength": len(compressed),
            "Metadata": {"sha256": page_digest},
        }

    monkeypatch.setattr(arxiv_manifest, "read_bytes", read_bytes)
    monkeypatch.setattr(arxiv_manifest, "head_object", head_object)

    capture = arxiv_manifest.load_capture(
        f"s3://landing/{manifest_key}",
        expected_source_date=source_day,
        raw_object_prefix="api/arxiv/raw/oai",
    )

    assert capture.pages == [b"<OAI-PMH />"]
    assert capture.page_objects == [(physical_page_key, page_digest)]
    assert capture.manifest_key == manifest_key
