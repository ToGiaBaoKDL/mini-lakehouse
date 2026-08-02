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
    assert "CALL glue.system.rewrite_data_files" in statements[0]
    assert "source_hour >= TIMESTAMP ''2026-07-29 00:00:00''" in statements[0]
    assert "partial-progress.enabled" in statements[0]
    assert "CALL glue.system.expire_snapshots" in statements[1]
    assert "retain_last => 5" in statements[1]
    assert "CALL glue.system.remove_orphan_files" in statements[2]
    assert all("stream_results => true" in statement for statement in statements[1:])


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
