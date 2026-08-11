from datetime import UTC, datetime

from emr_jobs.maintenance import POLICIES, maintenance_commands
from lakehouse.contracts import load_contracts


def test_partitioned_table_maintenance_bounds_data_rewrite_and_metadata_pruning() -> None:
    table = load_contracts().source("github_archive").table("events_raw")

    commands = maintenance_commands(
        table_name="landing_github_archive.events_raw",
        table=table,
        policy=POLICIES["landing"],
        as_of=datetime(2026, 8, 1, 12, tzinfo=UTC),
    )

    assert len(commands) == 3
    rewrite, rewrite_args = commands[0]
    assert "CALL glue.system.rewrite_data_files" in rewrite
    assert "where => :optimize_filter" in rewrite
    assert rewrite_args["optimize_filter"] == "source_hour >= TIMESTAMP '2026-07-29 00:00:00'"
    assert rewrite_args["target_file_size_bytes"] == str(256 * 1024 * 1024)
    assert "partial-progress.enabled" in rewrite
    snapshots, snapshot_args = commands[1]
    assert "CALL glue.system.expire_snapshots" in snapshots
    assert "retain_last => :retain_snapshots" in snapshots
    assert snapshot_args["retain_snapshots"] == 5
    orphans, _ = commands[2]
    assert "CALL glue.system.remove_orphan_files" in orphans
    assert all("stream_results => true" in statement for statement, _ in commands[1:])


def test_unpartitioned_table_maintenance_never_triggers_a_full_data_rewrite() -> None:
    table = load_contracts().curated_product("arxiv").table("papers")

    commands = maintenance_commands(
        table_name="curated_arxiv.papers",
        table=table,
        policy=POLICIES["curated"],
        as_of=datetime(2026, 8, 1, 12, tzinfo=UTC),
    )

    assert len(commands) == 2
    assert all("rewrite_data_files" not in statement for statement, _ in commands)
    assert commands[0][1]["retain_snapshots"] == 10


def test_date_partition_predicate_is_bound_without_sql_string_escaping() -> None:
    table = load_contracts().source("arxiv").table("oai_records_raw")

    commands = maintenance_commands(
        table_name="landing_arxiv.oai_records_raw",
        table=table,
        policy=POLICIES["landing"],
        as_of=datetime(2026, 8, 11, 3, tzinfo=UTC),
    )

    statement, arguments = commands[0]
    assert "where => :optimize_filter" in statement
    assert arguments["optimize_filter"] == "datestamp_date >= DATE '2026-08-08'"
