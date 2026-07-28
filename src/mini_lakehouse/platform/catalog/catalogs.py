from collections.abc import Mapping

from apache_polaris.sdk.management.api.polaris_default_api import PolarisDefaultApi
from apache_polaris.sdk.management.exceptions import NotFoundException
from apache_polaris.sdk.management.models import (
    AwsStorageConfigInfo,
    Catalog,
    CatalogProperties,
    CreateCatalogRequest,
    PolarisCatalog,
    UpdateCatalogRequest,
)

from mini_lakehouse.config import Settings
from mini_lakehouse.contracts import PlatformContracts
from mini_lakehouse.platform.catalog.layout import (
    catalog_allowed_locations,
    catalog_properties,
)

_REQUEST_TIMEOUT_SECONDS = 15.0


def _internal_catalog(catalog: Catalog) -> PolarisCatalog:
    if not isinstance(catalog, PolarisCatalog):
        raise RuntimeError(f"Polaris catalog {catalog.name!r} is not internal")
    if not isinstance(catalog.storage_config_info, AwsStorageConfigInfo):
        raise RuntimeError(f"Polaris catalog {catalog.name!r} does not use S3 storage")
    return catalog


def _sdk_properties(payload: Mapping[str, object]) -> CatalogProperties:
    properties = CatalogProperties.from_dict(dict(payload))
    if properties is None:
        raise RuntimeError("Polaris SDK did not create catalog properties")
    return properties


def _storage_config(
    settings: Settings,
    allowed_locations: tuple[str, ...],
) -> AwsStorageConfigInfo:
    storage = settings.storage
    return AwsStorageConfigInfo.model_validate(
        {
            "storageType": "S3",
            "allowedLocations": list(allowed_locations),
            "endpoint": storage.endpoints.external_url,
            "endpointInternal": storage.endpoints.internal_url,
            "pathStyleAccess": storage.path_style_access,
            "region": storage.region,
            "stsUnavailable": storage.sts_unavailable,
            "kmsUnavailable": storage.kms_unavailable,
        }
    )


def load_catalog(api: PolarisDefaultApi, name: str) -> PolarisCatalog | None:
    try:
        return _internal_catalog(api.get_catalog(name, _request_timeout=_REQUEST_TIMEOUT_SECONDS))
    except NotFoundException:
        return None


def catalog_drift(
    current: PolarisCatalog,
    settings: Settings,
    contracts: PlatformContracts,
) -> tuple[str, ...]:
    desired_properties = catalog_properties(settings, contracts)
    desired_locations = catalog_allowed_locations(settings, contracts)
    current_properties = current.properties.to_dict()
    current_storage = current.storage_config_info
    if not isinstance(current_storage, AwsStorageConfigInfo):
        return ("storage.type",)
    expected_storage = {
        "allowed_locations": set(desired_locations),
        "endpoint": settings.storage.endpoints.external_url,
        "internal_endpoint": settings.storage.endpoints.internal_url,
        "path_style_access": settings.storage.path_style_access,
        "region": settings.storage.region,
        "sts_unavailable": settings.storage.sts_unavailable,
        "kms_unavailable": settings.storage.kms_unavailable,
    }
    current_values = {
        "allowed_locations": set(current_storage.allowed_locations or ()),
        "endpoint": current_storage.endpoint,
        "internal_endpoint": current_storage.endpoint_internal,
        "path_style_access": bool(current_storage.path_style_access),
        "region": current_storage.region,
        "sts_unavailable": current_storage.sts_unavailable,
        "kms_unavailable": current_storage.kms_unavailable,
    }
    drift = [
        f"properties.{name}"
        for name in sorted(current_properties.keys() | desired_properties.keys())
        if current_properties.get(name) != desired_properties.get(name)
    ]
    drift.extend(
        f"storage.{name}"
        for name, value in expected_storage.items()
        if current_values[name] != value
    )
    if current.type != contracts.platform.catalog.type:
        drift.insert(0, "type")
    return tuple(drift)


def bootstrap_catalog(
    api: PolarisDefaultApi,
    settings: Settings,
    contracts: PlatformContracts,
) -> None:
    name = contracts.platform.catalog.name
    properties = catalog_properties(settings, contracts)
    locations = catalog_allowed_locations(settings, contracts)
    current = load_catalog(api, name)
    if current is None:
        api.create_catalog(
            CreateCatalogRequest(
                catalog=PolarisCatalog.model_validate(
                    {
                        "type": contracts.platform.catalog.type,
                        "name": name,
                        "properties": _sdk_properties(properties),
                        "storageConfigInfo": _storage_config(settings, locations),
                    }
                )
            ),
            _request_timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        return

    drift = catalog_drift(current, settings, contracts)
    if not drift:
        return
    if "type" in drift:
        raise RuntimeError(f"Polaris catalog {name!r} requires an explicit migration: type")
    if current.entity_version is None:
        raise RuntimeError(f"Polaris catalog {name!r} has no entity version")
    api.update_catalog(
        name,
        UpdateCatalogRequest.model_validate(
            {
                "currentEntityVersion": current.entity_version,
                "properties": properties,
                "storageConfigInfo": _storage_config(settings, locations),
            }
        ),
        _request_timeout=_REQUEST_TIMEOUT_SECONDS,
    )


def validate_catalog(
    api: PolarisDefaultApi,
    settings: Settings,
    contracts: PlatformContracts,
) -> tuple[str, ...]:
    name = contracts.platform.catalog.name
    current = load_catalog(api, name)
    if current is None:
        return (f"catalog:{name}:missing",)
    return tuple(f"catalog:{name}:{item}" for item in catalog_drift(current, settings, contracts))
