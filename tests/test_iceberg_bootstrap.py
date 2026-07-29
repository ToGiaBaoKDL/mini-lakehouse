from typing import Any, cast
from unittest.mock import create_autospec

import pytest
from pyiceberg.catalog import Catalog
from pyiceberg.partitioning import PartitionSpec
from pyiceberg.table import Table

from lakehouse_platform.contracts import ManagedIcebergTableContract, load_contracts
from lakehouse_platform.platform.catalog.layout import managed_tables
from lakehouse_platform.platform.catalog.schema import iceberg_schema, partition_spec
from lakehouse_platform.platform.catalog.tables import TABLE_PROPERTIES, apply_table


def _binding(identifier: tuple[str, ...]):
    return next(
        binding
        for binding in managed_tables(
            load_contracts(),
            landing_uri="s3://test-landing",
            curated_uri="s3://test-curated",
        )
        if binding[0].iceberg == identifier
    )


def _matching_table(contract: ManagedIcebergTableContract, location: str) -> Table:
    table = create_autospec(Table, instance=True)
    table.location.return_value = location
    table.format_version = 2
    table.schema.return_value = iceberg_schema(contract.columns, contract.primary_key)
    table.spec.return_value = partition_spec(contract.columns, contract.partitioning)
    table.properties = dict(TABLE_PROPERTIES)
    return table


def test_apply_creates_missing_table_from_contract() -> None:
    identifier, location, contract = _binding(("landing_github_archive", "events_raw"))
    catalog = create_autospec(Catalog, instance=True)
    table = _matching_table(contract, location)
    catalog.create_table_if_not_exists.return_value = table

    apply_table(catalog, identifier, location, contract)

    request = catalog.create_table_if_not_exists.call_args.kwargs
    assert request["identifier"] == ("landing_github_archive", "events_raw")
    assert request["location"].endswith("/api/github_archive/tables/events_raw")
    assert request["properties"] == {"format-version": "2", **TABLE_PROPERTIES}
    cast(Any, table.transaction).assert_not_called()


def test_apply_never_auto_migrates_schema_or_partition_drift() -> None:
    identifier, location, contract = _binding(("curated_github", "events"))
    catalog = create_autospec(Catalog, instance=True)
    table = _matching_table(contract, location)
    cast(Any, table.spec).return_value = PartitionSpec()
    catalog.create_table_if_not_exists.return_value = table

    with pytest.raises(RuntimeError, match=r"explicit migration.*partition_spec"):
        apply_table(catalog, identifier, location, contract)

    cast(Any, table.transaction).assert_not_called()


def test_curated_primary_keys_are_iceberg_identifier_fields() -> None:
    _, _, contract = _binding(("curated_arxiv", "papers"))
    schema = iceberg_schema(contract.columns, contract.primary_key)

    assert schema.identifier_field_ids == [1]
