"""Apply and validate YAML table contracts through the PyIceberg SDK."""

from collections.abc import Mapping

from pyiceberg.catalog import Catalog
from pyiceberg.schema import Schema
from pyiceberg.table import Table

from lakehouse.catalog.layout import managed_tables, namespace_properties
from lakehouse.catalog.schema import iceberg_schema, partition_spec
from lakehouse.contracts import (
    DataContracts,
    ManagedIcebergTableContract,
    TableIdentifier,
)

FORMAT_VERSION = 2
MANAGED_BY_PROPERTY = "managed_by"
MANAGED_BY_VALUE = "lakehouse-platform"
TABLE_PROPERTIES = {
    MANAGED_BY_PROPERTY: MANAGED_BY_VALUE,
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


def _compatible_schema_update(current: Schema, expected: Schema) -> bool:
    """Allow only append-only optional fields and required-to-optional relaxation."""
    if len(current.fields) > len(expected.fields):
        return False
    for current_field, expected_field in zip(current.fields, expected.fields, strict=False):
        if (
            current_field.field_id != expected_field.field_id
            or current_field.name != expected_field.name
            or current_field.field_type != expected_field.field_type
            or (current_field.optional and expected_field.required)
        ):
            return False
    return all(field.optional for field in expected.fields[len(current.fields) :])


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
    expected_schema = iceberg_schema(contract.columns, contract.primary_key)
    if catalog.table_exists(identifier.iceberg):
        table = catalog.load_table(identifier.iceberg)
    else:
        table = catalog.create_table(
            identifier=identifier.iceberg,
            schema=expected_schema,
            location=location,
            partition_spec=partition_spec(contract.columns, contract.partitioning),
            properties={"format-version": str(FORMAT_VERSION), **TABLE_PROPERTIES},
        )
    drift = table_drift(table, location, contract)
    unsafe = [item for item in drift if item != "schema" and not item.startswith("properties.")]
    if unsafe:
        raise RuntimeError(
            f"Iceberg table {'.'.join(identifier.iceberg)} requires an explicit migration: "
            + ", ".join(unsafe)
        )
    if "schema" in drift:
        if not _compatible_schema_update(table.schema(), expected_schema):
            raise RuntimeError(
                f"Iceberg table {'.'.join(identifier.iceberg)} requires an explicit migration: "
                "schema"
            )
        update = table.update_schema()
        update.union_by_name(expected_schema)
        update.commit()
        table.refresh()
        if "schema" in table_drift(table, location, contract):
            raise RuntimeError(
                f"Iceberg table {'.'.join(identifier.iceberg)} schema remains incompatible "
                "after safe reconciliation"
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
        properties = {
            **namespace_properties(namespace),
            MANAGED_BY_PROPERTY: MANAGED_BY_VALUE,
        }
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
    expected_namespaces = {namespace.path for namespace in contracts.managed_namespaces()}
    expected_tables = {
        identifier.iceberg
        for identifier, _, _ in managed_tables(
            contracts,
            landing_uri=landing_uri,
            curated_uri=curated_uri,
        )
    }
    existing_namespaces = set()
    for namespace in contracts.managed_namespaces():
        rendered = namespace.path[0]
        if not catalog.namespace_exists(namespace.path):
            errors.append(f"namespace:{rendered}:missing")
            continue
        existing_namespaces.add(namespace.path)
        expected = {
            **namespace_properties(namespace),
            MANAGED_BY_PROPERTY: MANAGED_BY_VALUE,
        }
        current = catalog.load_namespace_properties(namespace.path)
        errors.extend(
            f"namespace:{rendered}:properties.{key}"
            for key, value in expected.items()
            if current.get(key) != value
        )
    for namespace in catalog.list_namespaces():
        if namespace in expected_namespaces:
            continue
        properties = catalog.load_namespace_properties(namespace)
        if properties.get(MANAGED_BY_PROPERTY) == MANAGED_BY_VALUE:
            errors.append(f"namespace:{'.'.join(namespace)}:unexpected")

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
    for namespace in existing_namespaces:
        for identifier in catalog.list_tables(namespace):
            if identifier in expected_tables:
                continue
            table = catalog.load_table(identifier)
            if table.properties.get(MANAGED_BY_PROPERTY) == MANAGED_BY_VALUE:
                errors.append(f"table:{'.'.join(identifier)}:unexpected")
    return tuple(sorted(errors))
