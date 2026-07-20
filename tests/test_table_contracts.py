from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import create_autospec

import pyarrow as pa
import pytest
from pyiceberg.catalog import Catalog
from pyiceberg.table import Table

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import TableIdentifier
from mini_lakehouse.storage.iceberg import LandingEventsRepository


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


@pytest.mark.parametrize("part", ["bad-name", "bad.name", 'bad"name', ""])
def test_table_contract_rejects_unsafe_identifier_parts(part: str) -> None:
    with pytest.raises(ValueError, match="Invalid catalog identifier"):
        TableIdentifier(namespace=("analytics", part), name="events")


def test_landing_append_rejects_mixed_source_hours_before_writing() -> None:
    expected = datetime(2025, 1, 2, 3, tzinfo=UTC)
    events = pa.table(
        {
            "source_hour": [
                expected,
                datetime(2025, 1, 2, 4, tzinfo=UTC),
            ]
        }
    )
    repository = object.__new__(LandingEventsRepository)

    with pytest.raises(ValueError, match="Every landing row"):
        repository.append_hour(events, expected)


def test_existing_hour_reports_its_append_snapshot_not_the_current_snapshot() -> None:
    source_hour = datetime(2025, 1, 2, 3, tzinfo=UTC)
    catalog = create_autospec(Catalog, instance=True)
    table = create_autospec(Table, instance=True)
    catalog.table_exists.return_value = True
    catalog.load_table.return_value = table
    table.scan.return_value.count.return_value = 42
    table.snapshots.return_value = [
        SimpleNamespace(
            snapshot_id=7,
            summary={"source-hour": source_hour.isoformat()},
        ),
        SimpleNamespace(
            snapshot_id=8,
            summary={"source-hour": datetime(2025, 1, 2, 4, tzinfo=UTC).isoformat()},
        ),
    ]
    repository = LandingEventsRepository(Settings(), catalog=catalog)

    state = repository.hour_state(source_hour)

    assert state is not None
    assert state.row_count == 42
    assert state.snapshot_id == 7
