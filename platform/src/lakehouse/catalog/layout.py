"""Resolve contract-owned Glue objects to deterministic S3 locations."""

from collections.abc import Iterator
from typing import TypeAlias

from lakehouse.contracts import (
    DataContracts,
    ManagedIcebergTableContract,
    TableIdentifier,
)
from lakehouse.contracts.registry import NamespaceContract


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
DeclaredTableBinding: TypeAlias = tuple[  # noqa: UP040
    str,
    TableIdentifier,
    str,
    ManagedIcebergTableContract,
]


def declared_tables(contracts: DataContracts) -> Iterator[DeclaredTableBinding]:
    """Yield every contract-owned table once with its tier-relative location."""
    for source in contracts.sources:
        for table in source.tables:
            yield (
                "landing",
                source.table_identifier(table.key),
                f"{source.storage_prefix}/tables/{table.name}",
                table,
            )
    for product in contracts.curated:
        for table in product.tables:
            yield (
                "curated",
                product.table_identifier(table.key),
                f"{product.name}/tables/{table.name}",
                table,
            )


def managed_tables(
    contracts: DataContracts,
    *,
    landing_uri: str,
    curated_uri: str,
) -> Iterator[ManagedTableBinding]:
    seen_locations = set()
    roots = {"landing": landing_uri.rstrip("/"), "curated": curated_uri.rstrip("/")}
    for tier, identifier, relative_location, table in declared_tables(contracts):
        location = f"{roots[tier]}/{relative_location}"
        if location in seen_locations:
            raise ValueError("Managed Iceberg table locations must be unique")
        seen_locations.add(location)
        yield identifier, location, table
