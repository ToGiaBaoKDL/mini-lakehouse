from typing import Any, cast
from unittest.mock import create_autospec

import pytest
from lakehouse.catalog.layout import managed_tables, namespace_properties
from lakehouse.catalog.schema import iceberg_schema, partition_spec
from lakehouse.catalog.tables import (
    MANAGED_BY_PROPERTY,
    MANAGED_BY_VALUE,
    TABLE_PROPERTIES,
    apply_table,
    validate_table_contracts,
)
from lakehouse.contracts import ManagedIcebergTableContract, load_contracts
from pyiceberg.catalog import Catalog
from pyiceberg.partitioning import PartitionSpec
from pyiceberg.table import Table


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


def test_validation_reports_stale_managed_objects_without_claiming_external_tables() -> None:
    contracts = load_contracts()
    bindings = tuple(
        managed_tables(
            contracts,
            landing_uri="s3://test-landing",
            curated_uri="s3://test-curated",
        )
    )
    catalog = create_autospec(Catalog, instance=True)
    namespaces = contracts.managed_namespaces()
    expected_namespace_properties = {
        namespace.path: {
            **namespace_properties(namespace),
            MANAGED_BY_PROPERTY: MANAGED_BY_VALUE,
        }
        for namespace in namespaces
    }
    stale_namespace = ("landing_removed",)
    catalog.namespace_exists.return_value = True
    catalog.list_namespaces.return_value = [
        *(namespace.path for namespace in namespaces),
        stale_namespace,
    ]

    def load_namespace_properties(namespace: tuple[str, ...]) -> dict[str, str]:
        if namespace == stale_namespace:
            return {MANAGED_BY_PROPERTY: MANAGED_BY_VALUE}
        return expected_namespace_properties[namespace]

    catalog.load_namespace_properties.side_effect = load_namespace_properties
    catalog.table_exists.return_value = True

    tables = {
        identifier.iceberg: _matching_table(contract, location)
        for identifier, location, contract in bindings
    }
    stale_table = ("landing_github_archive", "events_old")
    external_table = ("landing_github_archive", "analyst_scratch")
    stale = create_autospec(Table, instance=True)
    stale.properties = {MANAGED_BY_PROPERTY: MANAGED_BY_VALUE}
    external = create_autospec(Table, instance=True)
    external.properties = {}
    tables[stale_table] = stale
    tables[external_table] = external
    catalog.load_table.side_effect = tables.__getitem__

    tables_by_namespace: dict[tuple[str, ...], list[tuple[str, ...]]] = {
        namespace.path: [] for namespace in namespaces
    }
    for identifier, _, _ in bindings:
        tables_by_namespace[identifier.namespace].append(identifier.iceberg)
    tables_by_namespace[("landing_github_archive",)].extend([stale_table, external_table])
    catalog.list_tables.side_effect = tables_by_namespace.__getitem__

    errors = validate_table_contracts(
        catalog,
        contracts,
        landing_uri="s3://test-landing",
        curated_uri="s3://test-curated",
    )

    assert errors == (
        "namespace:landing_removed:unexpected",
        "table:landing_github_archive.events_old:unexpected",
    )
