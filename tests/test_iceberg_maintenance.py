from datetime import UTC, datetime

from emr_jobs.maintenance import POLICIES, maintenance_statements
from lakehouse.contracts import load_contracts


def test_partitioned_table_maintenance_bounds_data_rewrite_and_metadata_pruning() -> None:
    table = load_contracts().source("github_archive").table("events_raw")

    statements = maintenance_statements(
        table_name="landing_github_archive.events_raw",
        table=table,
        policy=POLICIES["landing"],
        as_of=datetime(2026, 8, 1, 12, tzinfo=UTC),
    )

    assert len(statements) == 3
    rewrite = statements[0]
    assert "CALL glue.system.rewrite_data_files" in rewrite
    assert "where => 'source_hour >= TIMESTAMP \\'2026-07-29 00:00:00\\''" in rewrite
    assert "'target-file-size-bytes', '268435456'" in rewrite
    assert "partial-progress.enabled" in rewrite
    snapshots = statements[1]
    assert "CALL glue.system.expire_snapshots" in snapshots
    assert "older_than => TIMESTAMP '2026-07-25 12:00:00'" in snapshots
    assert "retain_last => 5" in snapshots
    assert "stream_results => true" in snapshots
    orphans = statements[2]
    assert "CALL glue.system.remove_orphan_files" in orphans
    assert "stream_results" not in orphans


def test_unpartitioned_table_maintenance_never_triggers_a_full_data_rewrite() -> None:
    table = load_contracts().curated_product("arxiv").table("papers")

    statements = maintenance_statements(
        table_name="curated_arxiv.papers",
        table=table,
        policy=POLICIES["curated"],
        as_of=datetime(2026, 8, 1, 12, tzinfo=UTC),
    )

    assert len(statements) == 2
    assert all("rewrite_data_files" not in statement for statement in statements)
    assert "retain_last => 10" in statements[0]


def test_date_partition_predicate_is_rendered_as_a_valid_nested_string_literal() -> None:
    table = load_contracts().source("arxiv").table("oai_records_raw")

    statements = maintenance_statements(
        table_name="landing_arxiv.oai_records_raw",
        table=table,
        policy=POLICIES["landing"],
        as_of=datetime(2026, 8, 11, 3, tzinfo=UTC),
    )

    statement = statements[0]
    assert "where => 'datestamp_date >= DATE \\'2026-08-08\\''" in statement
    assert ":table_name" not in statement
    assert ":optimize_filter" not in statement
