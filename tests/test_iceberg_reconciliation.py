from typing import Any, cast
from unittest.mock import create_autospec

import pytest
from pyiceberg.catalog import Catalog
from pyiceberg.partitioning import PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table

from mini_lakehouse.config import Settings
from mini_lakehouse.contracts import iceberg_schema, load_contracts, partition_spec
from mini_lakehouse.platform.desired_state import DesiredManagedTable, compile_desired_state
from mini_lakehouse.platform.reconcile import reconcile_managed_table
from mini_lakehouse.storage.iceberg import (
    ICEBERG_DELETE_AFTER_COMMIT,
    ICEBERG_PREVIOUS_VERSIONS_MAX,
)


def _desired_table(
    identifier: tuple[str, ...] = ("landing", "github_archive_events_raw"),
) -> DesiredManagedTable:
    state = compile_desired_state(Settings(), load_contracts())
    return next(table for table in state.managed_tables if table.identifier == identifier)


def _matching_table(
    desired: DesiredManagedTable,
    *,
    properties: dict[str, str] | None = None,
) -> Table:
    table = create_autospec(Table, instance=True)
    table.location.return_value = desired.location
    table.format_version = 2
    table.schema.return_value = iceberg_schema(desired.columns)
    table.spec.return_value = partition_spec(desired.columns, desired.partitioning)
    table.properties = properties or {}
    return table


def test_platform_reconciliation_creates_a_missing_landing_table() -> None:
    contracts = load_contracts()
    desired = _desired_table()
    catalog = create_autospec(Catalog, instance=True)
    catalog.table_exists.return_value = False
    table = _matching_table(
        desired,
        properties={
            "write.format.default": "parquet",
            "write.parquet.compression-codec": "zstd",
            ICEBERG_DELETE_AFTER_COMMIT: "true",
            ICEBERG_PREVIOUS_VERSIONS_MAX: "30",
        },
    )
    catalog.create_table.return_value = table

    result = reconcile_managed_table(catalog, contracts, desired)

    assert result is table
    assert catalog.create_table.call_args.kwargs["identifier"] == desired.identifier
    assert catalog.create_table.call_args.kwargs["location"] == desired.location
    assert catalog.create_table.call_args.kwargs["properties"]["format-version"] == "2"
    cast(Any, table.transaction).assert_not_called()


def test_platform_reconciliation_updates_only_managed_table_properties() -> None:
    contracts = load_contracts()
    desired = _desired_table(("curated", "github", "events"))
    catalog = create_autospec(Catalog, instance=True)
    catalog.table_exists.return_value = True
    table = _matching_table(desired, properties={"unmanaged": "preserved"})
    catalog.load_table.return_value = table

    reconcile_managed_table(catalog, contracts, desired)

    cast(Any, table.transaction).return_value.set_properties.assert_called_once_with(
        {
            "write.format.default": "parquet",
            "write.parquet.compression-codec": "zstd",
            ICEBERG_DELETE_AFTER_COMMIT: "true",
            ICEBERG_PREVIOUS_VERSIONS_MAX: "30",
        }
    )


@pytest.mark.parametrize(
    ("attribute", "value", "message"),
    [
        ("location", "s3://curated/wrong", "location drifted"),
        ("schema", Schema(), "schema drifted"),
        ("spec", PartitionSpec(), "partition spec drifted"),
    ],
)
def test_platform_reconciliation_rejects_unsafe_table_drift(
    attribute: str,
    value: object,
    message: str,
) -> None:
    contracts = load_contracts()
    desired = _desired_table(("curated", "github", "events"))
    catalog = create_autospec(Catalog, instance=True)
    catalog.table_exists.return_value = True
    table = _matching_table(desired)
    getattr(table, attribute).return_value = value
    catalog.load_table.return_value = table

    with pytest.raises(RuntimeError, match=message):
        reconcile_managed_table(catalog, contracts, desired)

    cast(Any, table.transaction).assert_not_called()


def test_platform_reconciliation_rejects_legacy_iceberg_format() -> None:
    contracts = load_contracts()
    desired = _desired_table(("curated", "github", "events"))
    catalog = create_autospec(Catalog, instance=True)
    catalog.table_exists.return_value = True
    table = _matching_table(desired)
    cast(Any, table).format_version = 1
    catalog.load_table.return_value = table

    with pytest.raises(RuntimeError, match="format version drifted"):
        reconcile_managed_table(catalog, contracts, desired)
