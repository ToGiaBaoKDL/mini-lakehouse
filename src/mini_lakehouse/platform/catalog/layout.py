"""Resolve contract-owned catalog objects to their physical storage layout."""

from collections.abc import Iterator
from typing import cast

from mini_lakehouse.config import Settings
from mini_lakehouse.contracts import (
    ManagedIcebergTableContract,
    PlatformContracts,
    TableIdentifier,
)
from mini_lakehouse.contracts.base import LIFECYCLE_TIERS, NamespacePath, StorageTier
from mini_lakehouse.contracts.sources import SourceContract


def storage_uri(settings: Settings, tier: StorageTier) -> str:
    return {
        "landing": settings.storage.landing_uri,
        "curated": settings.storage.curated_uri,
        "analytics": settings.storage.analytics_uri,
    }[tier]


def namespace_storage_uri(settings: Settings, namespace: NamespacePath) -> str:
    base_uri = storage_uri(settings, cast(StorageTier, namespace[0])).rstrip("/")
    if len(namespace) == 1:
        return base_uri
    return f"{base_uri}/{'/'.join(namespace[1:])}/"


def namespace_table_storage_uri(
    settings: Settings,
    namespace: NamespacePath,
    table_name: str,
) -> str:
    """Canonical table root for a namespace with dedicated physical ownership."""
    namespace_root = namespace_storage_uri(settings, namespace).rstrip("/")
    return f"{namespace_root}/{table_name}"


def source_table_storage_uri(
    settings: Settings,
    source: SourceContract,
    table_key: str,
) -> str:
    """Canonical physical location for a table in the shared landing namespace."""
    landing_root = storage_uri(settings, "landing").rstrip("/")
    return f"{landing_root}/{source.table_storage_prefix(table_key)}"


def catalog_properties(settings: Settings, contracts: PlatformContracts) -> dict[str, str]:
    spec = contracts.platform.catalog
    default_location = f"{storage_uri(settings, spec.default_storage_root).rstrip('/')}/_catalog"
    return {
        "owner": spec.owner,
        "default-base-location": default_location,
        "polaris.config.namespace-custom-location.enabled": str(
            spec.namespace_custom_locations
        ).lower(),
    }


def catalog_allowed_locations(
    settings: Settings,
    contracts: PlatformContracts,
) -> tuple[str, ...]:
    properties = catalog_properties(settings, contracts)
    return tuple(
        dict.fromkeys(
            (
                properties["default-base-location"],
                *(storage_uri(settings, tier) for tier in LIFECYCLE_TIERS),
            )
        )
    )


type ManagedTableBinding = tuple[
    TableIdentifier,
    str,
    ManagedIcebergTableContract,
]


def managed_tables(
    settings: Settings,
    contracts: PlatformContracts,
) -> Iterator[ManagedTableBinding]:
    seen_identifiers: set[tuple[str, ...]] = set()
    seen_locations: set[str] = set()
    for source in contracts.sources:
        for table in source.tables:
            identifier = source.table_identifier(table.key)
            location = source_table_storage_uri(settings, source, table.key)
            if identifier.iceberg in seen_identifiers or location in seen_locations:
                raise ValueError("Managed Iceberg table identifiers and locations must be unique")
            seen_identifiers.add(identifier.iceberg)
            seen_locations.add(location)
            yield identifier, location, table
    for product in contracts.curated:
        for table in product.tables:
            identifier = product.table_identifier(table.key)
            location = namespace_table_storage_uri(
                settings,
                product.curated_namespace,
                table.name,
            )
            if identifier.iceberg in seen_identifiers or location in seen_locations:
                raise ValueError("Managed Iceberg table identifiers and locations must be unique")
            seen_identifiers.add(identifier.iceberg)
            seen_locations.add(location)
            yield identifier, location, table


def validate_runtime_contract(settings: Settings, contracts: PlatformContracts) -> None:
    contract_catalog = contracts.platform.catalog.name
    configured_catalogs = {
        settings.polaris.catalog_name,
        settings.trino.catalog,
    }
    if configured_catalogs != {contract_catalog}:
        raise ValueError(
            "Polaris and Trino catalog settings must match the declarative catalog "
            f"{contract_catalog!r}; found {sorted(configured_catalogs)!r}"
        )
