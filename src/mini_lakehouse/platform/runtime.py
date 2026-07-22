from typing import cast

from mini_lakehouse.config import Settings
from mini_lakehouse.contracts import PlatformContracts
from mini_lakehouse.contracts.base import NamespacePath, StorageTier


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


def validate_runtime_contract(settings: Settings, contracts: PlatformContracts) -> None:
    contract_catalog = contracts.catalog.catalog.name
    configured_catalogs = {
        settings.polaris.catalog_name,
        settings.trino.catalog,
    }
    if configured_catalogs != {contract_catalog}:
        raise ValueError(
            "Polaris and Trino catalog settings must match the declarative catalog "
            f"{contract_catalog!r}; found {sorted(configured_catalogs)!r}"
        )
