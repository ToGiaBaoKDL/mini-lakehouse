from typing import Any, cast
from unittest.mock import create_autospec

import pytest
from pyiceberg.catalog import Catalog
from pyiceberg.partitioning import PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table

from mini_lakehouse.config import Settings
from mini_lakehouse.contracts import (
    ManagedIcebergTableContract,
    PlatformContracts,
    TableIdentifier,
    iceberg_schema,
    load_contracts,
    partition_spec,
)
from mini_lakehouse.platform.catalog.admin import bootstrap_table
from mini_lakehouse.platform.catalog.layout import managed_tables
from mini_lakehouse.platform.catalog.tables import managed_table_properties


def _binding(
    identifier: tuple[str, ...],
) -> tuple[PlatformContracts, TableIdentifier, str, ManagedIcebergTableContract]:
    contracts = load_contracts()
    table = next(
        binding
        for binding in managed_tables(Settings(), contracts)
        if binding[0].iceberg == identifier
    )
    return contracts, *table


def _matching_table(
    contract: ManagedIcebergTableContract,
    location: str,
    *,
    properties: dict[str, str] | None = None,
    primary_key: tuple[str, ...] | None = None,
) -> Table:
    table = create_autospec(Table, instance=True)
    table.location.return_value = location
    table.format_version = 2
    table.schema.return_value = iceberg_schema(
        contract.columns,
        contract.primary_key if primary_key is None else primary_key,
    )
    table.spec.return_value = partition_spec(contract.columns, contract.partitioning)
    table.properties = properties or {}
    return table


def _expected_properties(
    contracts: PlatformContracts,
    identifier: TableIdentifier,
) -> dict[str, str]:
    return managed_table_properties(contracts.maintenance.metadata_retention(identifier))


def test_bootstrap_creates_a_missing_table_with_contract_schema() -> None:
    contracts, identifier, location, contract = _binding(("landing", "github_archive_events_raw"))
    catalog = create_autospec(Catalog, instance=True)
    properties = _expected_properties(contracts, identifier)
    table = _matching_table(contract, location, properties=properties)
    catalog.create_table_if_not_exists.return_value = table

    result = bootstrap_table(catalog, contracts, identifier, location, contract)

    assert result is table
    request = catalog.create_table_if_not_exists.call_args.kwargs
    assert request["identifier"] == identifier.iceberg
    assert request["location"] == location
    assert request["schema"].identifier_field_ids == []
    assert request["properties"]["format-version"] == "2"
    cast(Any, table.transaction).assert_not_called()


def test_curated_primary_key_compiles_to_iceberg_identifier_fields() -> None:
    _, _, _, contract = _binding(("curated", "github", "events"))
    schema = iceberg_schema(contract.columns, contract.primary_key)
    field_ids = {schema.find_field(name).field_id for name in contract.primary_key}

    assert set(schema.identifier_field_ids) == field_ids


def test_bootstrap_adds_missing_identifier_fields_as_safe_metadata() -> None:
    contracts, identifier, location, contract = _binding(("curated", "github", "events"))
    properties = _expected_properties(contracts, identifier)
    table = _matching_table(
        contract,
        location,
        properties=properties,
        primary_key=(),
    )
    catalog = create_autospec(Catalog, instance=True)
    catalog.create_table_if_not_exists.return_value = table

    bootstrap_table(catalog, contracts, identifier, location, contract)

    update = cast(Any, table.update_schema).return_value.__enter__.return_value
    update.set_identifier_fields.assert_called_once_with(*contract.primary_key)


def test_bootstrap_updates_only_managed_table_properties() -> None:
    contracts, identifier, location, contract = _binding(("curated", "github", "events"))
    catalog = create_autospec(Catalog, instance=True)
    table = _matching_table(contract, location, properties={"unmanaged": "preserved"})
    catalog.create_table_if_not_exists.return_value = table

    bootstrap_table(catalog, contracts, identifier, location, contract)

    cast(Any, table.transaction).return_value.set_properties.assert_called_once_with(
        {
            "write.format.default": "parquet",
            "write.parquet.compression-codec": "zstd",
            "write.metadata.delete-after-commit.enabled": "true",
            "write.metadata.previous-versions-max": "30",
        }
    )


@pytest.mark.parametrize(
    ("attribute", "value", "message"),
    [
        ("location", "s3://curated/wrong", "location"),
        ("schema", Schema(), "schema"),
        ("spec", PartitionSpec(), "partition_spec"),
    ],
)
def test_bootstrap_rejects_drift_that_requires_an_explicit_migration(
    attribute: str,
    value: object,
    message: str,
) -> None:
    contracts, identifier, location, contract = _binding(("curated", "github", "events"))
    table = _matching_table(contract, location)
    getattr(table, attribute).return_value = value
    catalog = create_autospec(Catalog, instance=True)
    catalog.create_table_if_not_exists.return_value = table

    with pytest.raises(RuntimeError, match=message):
        bootstrap_table(catalog, contracts, identifier, location, contract)

    cast(Any, table.transaction).assert_not_called()
