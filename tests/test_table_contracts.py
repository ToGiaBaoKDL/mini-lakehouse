from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import create_autospec

import pyarrow as pa
import pytest
from pyiceberg.catalog import Catalog
from pyiceberg.expressions import EqualTo
from pyiceberg.partitioning import PartitionSpec
from pyiceberg.table import Table
from pyiceberg.transforms import HourTransform

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import TableIdentifier, load_contracts, partition_spec
from mini_lakehouse.sources.github_archive.repository import GithubArchiveRepository


def _events_partition_spec() -> PartitionSpec:
    events_contract = load_contracts().source("github_archive").table("events_raw")
    return partition_spec(events_contract.columns, events_contract.partitioning)


def test_nested_namespace_renders_as_one_trino_schema() -> None:
    table = TableIdentifier(
        namespace=("analytics", "engineering"),
        name="fct_repository_activity_daily",
    )

    assert table.iceberg == (
        "analytics",
        "engineering",
        "fct_repository_activity_daily",
    )
    assert table.trino("prod") == ('"prod"."analytics.engineering"."fct_repository_activity_daily"')


def test_github_archive_partition_spec_uses_hour_transform() -> None:
    field = _events_partition_spec().fields[0]

    assert field.source_id == 11
    assert field.field_id == 1000
    assert field.name == "archive_hour"
    assert isinstance(field.transform, HourTransform)


def test_landing_repository_requires_platform_bootstrap() -> None:
    catalog = create_autospec(Catalog, instance=True)
    catalog.table_exists.return_value = False
    repository = GithubArchiveRepository(Settings(), catalog=catalog)

    with pytest.raises(RuntimeError, match="run platform bootstrap first"):
        repository.hour_state(datetime(2025, 1, 2, 3, tzinfo=UTC))

    catalog.create_table.assert_not_called()


@pytest.mark.parametrize("part", ["bad-name", "bad.name", 'bad"name', ""])
def test_table_contract_rejects_unsafe_identifier_parts(part: str) -> None:
    with pytest.raises(ValueError, match="Invalid catalog identifier"):
        TableIdentifier(namespace=("analytics", part), name="events")


def test_landing_write_rejects_mixed_source_hours_before_writing() -> None:
    expected = datetime(2025, 1, 2, 3, tzinfo=UTC)
    events = pa.table(
        {
            "source_hour": [
                expected,
                datetime(2025, 1, 2, 4, tzinfo=UTC),
            ]
        }
    )
    repository = object.__new__(GithubArchiveRepository)

    with pytest.raises(ValueError, match="Every landing row"):
        repository.write_hour(events, expected)


def test_existing_hour_is_resolved_from_partition_metadata_without_reading_rows() -> None:
    source_hour = datetime(2025, 1, 2, 3, tzinfo=UTC)
    catalog = create_autospec(Catalog, instance=True)
    table = create_autospec(Table, instance=True)
    catalog.table_exists.return_value = True
    catalog.load_table.return_value = table
    table.scan.return_value.plan_files.return_value = [
        SimpleNamespace(file=SimpleNamespace(record_count=40)),
        SimpleNamespace(file=SimpleNamespace(record_count=2)),
    ]
    table.current_snapshot.return_value = SimpleNamespace(snapshot_id=8)
    repository = GithubArchiveRepository(Settings(), catalog=catalog)

    state = repository.hour_state(source_hour)

    assert state is not None
    assert state.row_count == 42
    assert state.snapshot_id == 8
    table.scan.return_value.to_arrow.assert_not_called()


def test_new_hour_uses_checkpoint_predicate_overwrite_as_the_idempotent_commit() -> None:
    source_hour = datetime(2025, 1, 2, 3, tzinfo=UTC)
    catalog = create_autospec(Catalog, instance=True)
    table = create_autospec(Table, instance=True)
    catalog.table_exists.return_value = True
    catalog.load_table.return_value = table
    table.scan.return_value.plan_files.return_value = []
    table.refresh.return_value.current_snapshot.return_value = SimpleNamespace(snapshot_id=9)
    repository = GithubArchiveRepository(Settings(), catalog=catalog)
    events = pa.table({"source_hour": [source_hour]})

    result = repository.write_hour(events, source_hour)

    assert result.was_written is True
    assert result.snapshot_id == 9
    table.overwrite.assert_called_once()
    assert isinstance(table.overwrite.call_args.kwargs["overwrite_filter"], EqualTo)
    table.append.assert_not_called()
