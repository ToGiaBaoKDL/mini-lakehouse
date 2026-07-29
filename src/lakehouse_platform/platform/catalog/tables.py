"""Apply and validate YAML table contracts through the PyIceberg SDK."""

from collections.abc import Mapping

from pyiceberg.catalog import Catalog
from pyiceberg.table import Table

from lakehouse_platform.contracts import (
    DataContracts,
    ManagedIcebergTableContract,
    TableIdentifier,
)
from lakehouse_platform.platform.catalog.layout import managed_tables, namespace_properties
from lakehouse_platform.platform.catalog.schema import iceberg_schema, partition_spec

FORMAT_VERSION = 2
TABLE_PROPERTIES = {
    "write.format.default": "parquet",
    "write.parquet.compression-codec": "zstd",
    "write.metadata.delete-after-commit.enabled": "true",
    "write.metadata.previous-versions-max": "30",
}


def _schema_fingerprint(table: Table) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (field.field_id, field.name, field.field_type, field.required)
        for field in table.schema().fields
    )


def _expected_schema(
    contract: ManagedIcebergTableContract,
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (field.field_id, field.name, field.field_type, field.required)
        for field in iceberg_schema(contract.columns, contract.primary_key).fields
    )


def _partition_fingerprint(table: Table) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (field.source_id, field.field_id, field.transform, field.name)
        for field in table.spec().fields
    )


def _expected_partition(
    contract: ManagedIcebergTableContract,
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (field.source_id, field.field_id, field.transform, field.name)
        for field in partition_spec(contract.columns, contract.partitioning).fields
    )


def _identifier_fields(contract: ManagedIcebergTableContract) -> frozenset[int]:
    return frozenset(
        column.field_id for column in contract.columns if column.name in contract.primary_key
    )


def table_drift(
    table: Table,
    location: str,
    contract: ManagedIcebergTableContract,
    properties: Mapping[str, str] = TABLE_PROPERTIES,
) -> tuple[str, ...]:
    drift = []
    if table.location().rstrip("/") != location.rstrip("/"):
        drift.append("location")
    if table.format_version != FORMAT_VERSION:
        drift.append("format_version")
    if _schema_fingerprint(table) != _expected_schema(contract):
        drift.append("schema")
    if _partition_fingerprint(table) != _expected_partition(contract):
        drift.append("partition_spec")
    if frozenset(table.schema().identifier_field_ids) != _identifier_fields(contract):
        drift.append("identifier_fields")
    drift.extend(
        f"properties.{key}"
        for key, value in properties.items()
        if str(table.properties.get(key, "")).lower() != value
    )
    return tuple(drift)


def apply_table(
    catalog: Catalog,
    identifier: TableIdentifier,
    location: str,
    contract: ManagedIcebergTableContract,
) -> None:
    table = catalog.create_table_if_not_exists(
        identifier=identifier.iceberg,
        schema=iceberg_schema(contract.columns, contract.primary_key),
        location=location,
        partition_spec=partition_spec(contract.columns, contract.partitioning),
        properties={"format-version": str(FORMAT_VERSION), **TABLE_PROPERTIES},
    )
    drift = table_drift(table, location, contract)
    unsafe = [item for item in drift if not item.startswith("properties.")]
    if unsafe:
        raise RuntimeError(
            f"Iceberg table {'.'.join(identifier.iceberg)} requires an explicit migration: "
            + ", ".join(unsafe)
        )
    updates = {
        key: value
        for key, value in TABLE_PROPERTIES.items()
        if str(table.properties.get(key, "")).lower() != value
    }
    if updates:
        table.transaction().set_properties(updates).commit_transaction()


def apply_table_contracts(
    catalog: Catalog,
    contracts: DataContracts,
    *,
    landing_uri: str,
    curated_uri: str,
) -> None:
    for namespace in contracts.managed_namespaces():
        properties = namespace_properties(namespace)
        catalog.create_namespace_if_not_exists(namespace.path, properties)
        current = catalog.load_namespace_properties(namespace.path)
        updates = {key: value for key, value in properties.items() if current.get(key) != value}
        if updates:
            catalog.update_namespace_properties(namespace.path, updates=updates)
    for identifier, location, contract in managed_tables(
        contracts,
        landing_uri=landing_uri,
        curated_uri=curated_uri,
    ):
        apply_table(catalog, identifier, location, contract)


def validate_table_contracts(
    catalog: Catalog,
    contracts: DataContracts,
    *,
    landing_uri: str,
    curated_uri: str,
) -> tuple[str, ...]:
    errors = []
    existing_namespaces = set()
    for namespace in contracts.managed_namespaces():
        rendered = namespace.path[0]
        if not catalog.namespace_exists(namespace.path):
            errors.append(f"namespace:{rendered}:missing")
            continue
        existing_namespaces.add(namespace.path)
        expected = namespace_properties(namespace)
        current = catalog.load_namespace_properties(namespace.path)
        errors.extend(
            f"namespace:{rendered}:properties.{key}"
            for key, value in expected.items()
            if current.get(key) != value
        )
    for identifier, location, contract in managed_tables(
        contracts,
        landing_uri=landing_uri,
        curated_uri=curated_uri,
    ):
        rendered = ".".join(identifier.iceberg)
        if identifier.namespace not in existing_namespaces:
            continue
        if not catalog.table_exists(identifier.iceberg):
            errors.append(f"table:{rendered}:missing")
            continue
        errors.extend(
            f"table:{rendered}:{item}"
            for item in table_drift(
                catalog.load_table(identifier.iceberg),
                location,
                contract,
            )
        )
    return tuple(sorted(errors))
