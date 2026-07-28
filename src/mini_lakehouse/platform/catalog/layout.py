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
from mini_lakehouse.contracts.platform import NamespaceContract
from mini_lakehouse.contracts.sources import SourceContract


def _storage_uri(settings: Settings, tier: StorageTier) -> str:
    return {
        "landing": settings.storage.landing_uri,
        "curated": settings.storage.curated_uri,
        "analytics": settings.storage.analytics_uri,
    }[tier]


def _namespace_storage_uri(settings: Settings, namespace: NamespacePath) -> str:
    base_uri = _storage_uri(settings, cast(StorageTier, namespace[0])).rstrip("/")
    if len(namespace) == 1:
        return base_uri
    return f"{base_uri}/{'/'.join(namespace[1:])}/"


def _namespace_table_storage_uri(
    settings: Settings,
    namespace: NamespacePath,
    table_name: str,
) -> str:
    """Canonical table root for a namespace with dedicated physical ownership."""
    namespace_root = _namespace_storage_uri(settings, namespace).rstrip("/")
    return f"{namespace_root}/{table_name}"


def source_table_storage_uri(
    settings: Settings,
    source: SourceContract,
    table_key: str,
) -> str:
    """Canonical physical location for a table in the shared landing namespace."""
    landing_root = _storage_uri(settings, "landing").rstrip("/")
    return f"{landing_root}/{source.table_storage_prefix(table_key)}"


def namespace_properties(
    settings: Settings,
    namespace: NamespaceContract,
) -> dict[str, str]:
    return {
        **namespace.properties,
        "owner": namespace.owner,
        "location": _namespace_storage_uri(settings, namespace.path),
    }


def _catalog_default_location(settings: Settings, contracts: PlatformContracts) -> str:
    tier = contracts.platform.catalog.default_storage_root
    return f"{_storage_uri(settings, tier).rstrip('/')}/_catalog"


def catalog_properties(settings: Settings, contracts: PlatformContracts) -> dict[str, str]:
    spec = contracts.platform.catalog
    return {
        "owner": spec.owner,
        "default-base-location": _catalog_default_location(settings, contracts),
        "polaris.config.namespace-custom-location.enabled": str(
            spec.namespace_custom_locations
        ).lower(),
    }


def catalog_allowed_locations(
    settings: Settings,
    contracts: PlatformContracts,
) -> tuple[str, ...]:
    locations = (
        _catalog_default_location(settings, contracts),
        *(_storage_uri(settings, tier) for tier in LIFECYCLE_TIERS),
    )
    return tuple(dict.fromkeys(locations))


type _ManagedTableBinding = tuple[
    TableIdentifier,
    str,
    ManagedIcebergTableContract,
]


def managed_tables(
    settings: Settings,
    contracts: PlatformContracts,
) -> Iterator[_ManagedTableBinding]:
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
            location = _namespace_table_storage_uri(
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
    if settings.polaris.catalog_name != contract_catalog:
        raise ValueError(
            "Polaris catalog setting must match the declarative catalog "
            f"{contract_catalog!r}; found {settings.polaris.catalog_name!r}"
        )
