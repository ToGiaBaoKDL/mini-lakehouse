import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from mini_lakehouse.config import Settings
from mini_lakehouse.contracts.access import CatalogRoleGrantContract
from mini_lakehouse.contracts.base import LIFECYCLE_TIERS, ColumnContract, NamespacePath
from mini_lakehouse.contracts.registry import PlatformContracts
from mini_lakehouse.contracts.tables import IcebergPartitionContract
from mini_lakehouse.platform.runtime import (
    namespace_storage_uri,
    namespace_table_storage_uri,
    source_table_storage_uri,
    storage_uri,
    validate_runtime_contract,
)


@dataclass(frozen=True, slots=True)
class DesiredStorageConfig:
    external_endpoint: str | None
    internal_endpoint: str | None
    path_style_access: bool
    region: str
    sts_unavailable: bool
    kms_unavailable: bool
    allowed_locations: tuple[str, ...]
    storage_type: Literal["S3"] = "S3"

    def management_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "storageType": self.storage_type,
            "pathStyleAccess": self.path_style_access,
            "region": self.region,
            "stsUnavailable": self.sts_unavailable,
            "kmsUnavailable": self.kms_unavailable,
            "allowedLocations": list(self.allowed_locations),
        }
        if self.external_endpoint is not None:
            payload["endpoint"] = self.external_endpoint
        if self.internal_endpoint is not None:
            payload["endpointInternal"] = self.internal_endpoint
        return payload


@dataclass(frozen=True, slots=True)
class DesiredCatalog:
    name: str
    type: Literal["INTERNAL"]
    properties: tuple[tuple[str, str], ...]
    storage: DesiredStorageConfig

    def management_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "properties": dict(self.properties),
            "storageConfigInfo": self.storage.management_payload(),
        }


@dataclass(frozen=True, slots=True)
class DesiredNamespace:
    path: NamespacePath
    properties: tuple[tuple[str, str], ...]

    def iceberg_properties(self) -> dict[str, str]:
        return dict(self.properties)


@dataclass(frozen=True, slots=True)
class DesiredManagedTable:
    identifier: tuple[str, ...]
    location: str
    columns: tuple[ColumnContract, ...]
    partitioning: tuple[IcebergPartitionContract, ...]


@dataclass(frozen=True, slots=True)
class DesiredPlatformState:
    contract_digest: str
    catalog: DesiredCatalog
    namespaces: tuple[DesiredNamespace, ...]
    access_grants: tuple[CatalogRoleGrantContract, ...]
    managed_tables: tuple[DesiredManagedTable, ...]


def _contract_digest(contracts: PlatformContracts) -> str:
    payload = json.dumps(
        contracts.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _catalog_state(settings: Settings, contracts: PlatformContracts) -> DesiredCatalog:
    spec = contracts.platform.catalog
    default_base_location = (
        f"{storage_uri(settings, spec.default_storage_root).rstrip('/')}/_catalog"
    )
    properties = {
        "owner": spec.owner,
        "default-base-location": default_base_location,
        "polaris.config.namespace-custom-location.enabled": str(
            spec.namespace_custom_locations
        ).lower(),
        **spec.properties,
    }
    allowed_locations = tuple(
        dict.fromkeys(
            (
                default_base_location,
                *(storage_uri(settings, tier) for tier in LIFECYCLE_TIERS),
            )
        )
    )
    storage = settings.storage
    return DesiredCatalog(
        name=spec.name,
        type=spec.type,
        properties=tuple(sorted(properties.items())),
        storage=DesiredStorageConfig(
            external_endpoint=storage.endpoints.external_url,
            internal_endpoint=storage.endpoints.internal_url,
            path_style_access=storage.path_style_access,
            region=storage.region,
            sts_unavailable=storage.sts_unavailable,
            kms_unavailable=storage.kms_unavailable,
            allowed_locations=allowed_locations,
        ),
    )


def _namespace_states(
    settings: Settings,
    contracts: PlatformContracts,
) -> tuple[DesiredNamespace, ...]:
    states = (
        DesiredNamespace(
            path=namespace.path,
            properties=tuple(
                sorted(
                    namespace.iceberg_properties(
                        namespace_storage_uri(settings, namespace.path)
                    ).items()
                )
            ),
        )
        for namespace in contracts.managed_namespaces()
    )
    return tuple(sorted(states, key=lambda state: state.path))


def _managed_table_states(
    settings: Settings,
    contracts: PlatformContracts,
) -> tuple[DesiredManagedTable, ...]:
    tables: list[DesiredManagedTable] = []
    for source in contracts.sources:
        for table in source.tables:
            tables.append(
                DesiredManagedTable(
                    identifier=source.table_identifier(table.key).iceberg,
                    location=source_table_storage_uri(settings, source, table.key),
                    columns=table.columns,
                    partitioning=table.partitioning,
                )
            )
    for product in contracts.curated:
        for table in product.tables:
            tables.append(
                DesiredManagedTable(
                    identifier=product.table_identifier(table.key).iceberg,
                    location=namespace_table_storage_uri(
                        settings,
                        product.curated_namespace,
                        table.name,
                    ),
                    columns=table.columns,
                    partitioning=table.partitioning,
                )
            )
    tables.sort(key=lambda table: table.identifier)
    locations = [table.location for table in tables]
    if len(locations) != len(set(locations)):
        raise ValueError("Managed Iceberg table locations must be globally unique")
    return tuple(tables)


def compile_desired_state(
    settings: Settings,
    contracts: PlatformContracts,
) -> DesiredPlatformState:
    validate_runtime_contract(settings, contracts)
    return DesiredPlatformState(
        contract_digest=_contract_digest(contracts),
        catalog=_catalog_state(settings, contracts),
        namespaces=_namespace_states(settings, contracts),
        access_grants=tuple(
            sorted(
                contracts.access.catalog_role_grants,
                key=lambda grant: grant.catalog_role,
            )
        ),
        managed_tables=_managed_table_states(settings, contracts),
    )
