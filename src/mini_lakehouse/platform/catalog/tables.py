from collections.abc import Mapping

from pyiceberg.catalog import Catalog
from pyiceberg.table import Table

from mini_lakehouse.config import Settings
from mini_lakehouse.contracts import (
    ManagedIcebergTableContract,
    PlatformContracts,
    TableIdentifier,
    iceberg_schema,
    partition_spec,
)
from mini_lakehouse.contracts.maintenance import MetadataRetentionContract
from mini_lakehouse.platform.catalog.layout import (
    managed_tables,
    namespace_properties,
)
from mini_lakehouse.storage.iceberg import load_iceberg_catalog

MANAGED_ICEBERG_FORMAT_VERSION = 2

_MANAGED_TABLE_DEFAULTS = {
    "write.format.default": "parquet",
    "write.parquet.compression-codec": "zstd",
}
_DELETE_AFTER_COMMIT = "write.metadata.delete-after-commit.enabled"
_PREVIOUS_VERSIONS_MAX = "write.metadata.previous-versions-max"


def metadata_retention_properties(
    retention: MetadataRetentionContract,
) -> dict[str, str]:
    return {
        _DELETE_AFTER_COMMIT: str(retention.delete_after_commit).lower(),
        _PREVIOUS_VERSIONS_MAX: str(retention.previous_versions_max),
    }


def managed_table_properties(
    retention: MetadataRetentionContract,
) -> dict[str, str]:
    return {
        **_MANAGED_TABLE_DEFAULTS,
        **metadata_retention_properties(retention),
    }


def metadata_retention_is_current(
    properties: Mapping[str, str],
    retention: MetadataRetentionContract,
) -> bool:
    expected = metadata_retention_properties(retention)
    return all(str(properties.get(key, "")).lower() == value for key, value in expected.items())


def _schema_fingerprint(table: Table) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (field.field_id, field.name, field.field_type, field.required)
        for field in table.schema().fields
    )


def _expected_schema_fingerprint(
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


def _expected_partition_fingerprint(
    contract: ManagedIcebergTableContract,
) -> tuple[tuple[object, ...], ...]:
    spec = partition_spec(contract.columns, contract.partitioning)
    return tuple(
        (field.source_id, field.field_id, field.transform, field.name) for field in spec.fields
    )


def _expected_identifier_fields(
    contract: ManagedIcebergTableContract,
) -> frozenset[int]:
    return frozenset(
        column.field_id for column in contract.columns if column.name in contract.primary_key
    )


def _table_properties(
    contracts: PlatformContracts,
    identifier: TableIdentifier,
) -> dict[str, str]:
    return managed_table_properties(contracts.maintenance.metadata_retention(identifier))


def table_drift(
    table: Table,
    location: str,
    contract: ManagedIcebergTableContract,
    properties: dict[str, str],
) -> tuple[str, ...]:
    drift: list[str] = []
    if table.location().rstrip("/") != location.rstrip("/"):
        drift.append("location")
    if table.format_version != MANAGED_ICEBERG_FORMAT_VERSION:
        drift.append("format_version")
    if _schema_fingerprint(table) != _expected_schema_fingerprint(contract):
        drift.append("schema")
    if _partition_fingerprint(table) != _expected_partition_fingerprint(contract):
        drift.append("partition_spec")
    if frozenset(table.schema().identifier_field_ids) != _expected_identifier_fields(contract):
        drift.append("identifier_fields")
    drift.extend(
        f"properties.{name}"
        for name, value in properties.items()
        if str(table.properties.get(name, "")).lower() != value
    )
    return tuple(drift)


def bootstrap_table(
    catalog: Catalog,
    contracts: PlatformContracts,
    identifier: TableIdentifier,
    location: str,
    contract: ManagedIcebergTableContract,
) -> Table:
    properties = _table_properties(contracts, identifier)
    table = catalog.create_table_if_not_exists(
        identifier=identifier.iceberg,
        schema=iceberg_schema(contract.columns, contract.primary_key),
        location=location,
        partition_spec=partition_spec(contract.columns, contract.partitioning),
        properties={
            "format-version": str(MANAGED_ICEBERG_FORMAT_VERSION),
            **properties,
        },
    )
    drift = set(table_drift(table, location, contract, properties))
    unsafe = {item for item in drift if not item.startswith("properties.")}
    safe_identifier_update = (
        unsafe == {"identifier_fields"}
        and not table.schema().identifier_field_ids
        and bool(contract.primary_key)
    )
    if unsafe and not safe_identifier_update:
        raise RuntimeError(
            f"Iceberg table {'.'.join(identifier.iceberg)} requires an explicit migration: "
            f"{', '.join(sorted(unsafe))}"
        )
    if safe_identifier_update:
        with table.update_schema() as update:
            update.set_identifier_fields(*contract.primary_key)
        table.refresh()
    updates = {
        name: value
        for name, value in properties.items()
        if str(table.properties.get(name, "")).lower() != value
    }
    if updates:
        table.transaction().set_properties(updates).commit_transaction()
    return table


def bootstrap_iceberg(
    settings: Settings,
    contracts: PlatformContracts,
) -> None:
    with load_iceberg_catalog(settings) as catalog:
        for namespace in contracts.managed_namespaces():
            properties = namespace_properties(settings, namespace)
            catalog.create_namespace_if_not_exists(namespace.path, properties)
            current = catalog.load_namespace_properties(namespace.path)
            updates = {
                name: value for name, value in properties.items() if current.get(name) != value
            }
            removals = set(current) - set(properties)
            if updates or removals:
                catalog.update_namespace_properties(
                    namespace.path,
                    removals=removals,
                    updates=updates,
                )
        for identifier, location, contract in managed_tables(settings, contracts):
            bootstrap_table(catalog, contracts, identifier, location, contract)


def validate_iceberg(
    settings: Settings,
    contracts: PlatformContracts,
) -> tuple[tuple[str, ...], set[tuple[str, ...]], set[tuple[str, ...]]]:
    errors: list[str] = []
    existing_namespaces: set[tuple[str, ...]] = set()
    existing_tables: set[tuple[str, ...]] = set()
    with load_iceberg_catalog(settings) as catalog:
        for namespace in contracts.managed_namespaces():
            rendered = ".".join(namespace.path)
            if not catalog.namespace_exists(namespace.path):
                errors.append(f"namespace:{rendered}:missing")
                continue
            existing_namespaces.add(namespace.path)
            expected = namespace_properties(settings, namespace)
            current = catalog.load_namespace_properties(namespace.path)
            errors.extend(
                f"namespace:{rendered}:properties.{name}"
                for name in sorted(current.keys() | expected.keys())
                if current.get(name) != expected.get(name)
            )

        for identifier, location, contract in managed_tables(settings, contracts):
            rendered = ".".join(identifier.iceberg)
            if identifier.namespace not in existing_namespaces:
                continue
            if not catalog.table_exists(identifier.iceberg):
                errors.append(f"table:{rendered}:missing")
                continue
            existing_tables.add(identifier.iceberg)
            table = catalog.load_table(identifier.iceberg)
            errors.extend(
                f"table:{rendered}:{item}"
                for item in table_drift(
                    table,
                    location,
                    contract,
                    _table_properties(contracts, identifier),
                )
            )
    return tuple(errors), existing_namespaces, existing_tables
