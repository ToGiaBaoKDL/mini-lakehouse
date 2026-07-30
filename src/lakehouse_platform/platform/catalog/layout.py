"""Resolve contract-owned Glue objects to deterministic S3 locations."""

from collections.abc import Iterator
from typing import TypeAlias

from lakehouse_platform.contracts import (
    DataContracts,
    ManagedIcebergTableContract,
    TableIdentifier,
)
from lakehouse_platform.contracts.registry import NamespaceContract


def namespace_properties(
    namespace: NamespaceContract,
) -> dict[str, str]:
    return {
        **namespace.properties,
        "owner": namespace.owner,
    }


ManagedTableBinding: TypeAlias = tuple[  # noqa: UP040
    TableIdentifier,
    str,
    ManagedIcebergTableContract,
]


def managed_tables(
    contracts: DataContracts,
    *,
    landing_uri: str,
    curated_uri: str,
) -> Iterator[ManagedTableBinding]:
    seen_locations = set()
    for source in contracts.sources:
        for table in source.tables:
            identifier = source.table_identifier(table.key)
            location = f"{landing_uri.rstrip('/')}/{source.storage_prefix}/tables/{table.name}"
            if location in seen_locations:
                raise ValueError("Managed Iceberg table locations must be unique")
            seen_locations.add(location)
            yield identifier, location, table
    for product in contracts.curated:
        for table in product.tables:
            identifier = product.table_identifier(table.key)
            location = f"{curated_uri.rstrip('/')}/{product.name}/tables/{table.name}"
            if location in seen_locations:
                raise ValueError("Managed Iceberg table locations must be unique")
            seen_locations.add(location)
            yield identifier, location, table
