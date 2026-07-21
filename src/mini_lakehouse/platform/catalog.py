import logging
from collections.abc import Mapping
from typing import Any, cast

from mini_lakehouse.config import Settings
from mini_lakehouse.contracts import PlatformContracts, load_contracts
from mini_lakehouse.platform.polaris import PolarisManagementClient
from mini_lakehouse.platform.runtime import storage_uri, validate_runtime_contract

logger = logging.getLogger(__name__)


def catalog_contract(
    settings: Settings,
    contracts: PlatformContracts | None = None,
) -> dict[str, Any]:
    registry = contracts or load_contracts(settings.contracts_dir)
    validate_runtime_contract(settings, registry)
    storage = settings.storage
    catalog = registry.catalog.catalog
    external_endpoint = storage.endpoint_url or "https://s3.amazonaws.com"
    internal_endpoint = external_endpoint
    default_base_location = (
        f"{storage_uri(settings, catalog.default_storage_root).rstrip('/')}/_catalog"
    )
    properties = {
        "owner": catalog.owner,
        "default-base-location": default_base_location,
        "polaris.config.namespace-custom-location.enabled": str(
            catalog.namespace_custom_locations
        ).lower(),
        **catalog.properties,
    }
    return {
        "name": catalog.name,
        "type": catalog.type,
        "properties": properties,
        "storageConfigInfo": {
            "storageType": "S3",
            "endpoint": external_endpoint,
            "endpointInternal": internal_endpoint,
            "pathStyleAccess": storage.path_style_access,
            "region": storage.region,
            "stsUnavailable": storage.sts_unavailable,
            "kmsUnavailable": storage.kms_unavailable,
            "allowedLocations": [
                default_base_location,
                *(storage_uri(settings, tier) for tier in ("landing", "curated", "analytics")),
            ],
        },
    }


def _catalog_drift(current_payload: object, desired: Mapping[str, Any]) -> list[str]:
    if not isinstance(current_payload, dict):
        return ["response is not a JSON object"]
    current = current_payload.get("catalog", current_payload)
    if not isinstance(current, dict):
        return ["response does not contain a catalog object"]
    drift: list[str] = []
    for key in ("name", "type"):
        if current.get(key) != desired[key]:
            drift.append(key)

    current_properties = current.get("properties")
    desired_properties = cast(dict[str, object], desired["properties"])
    if not isinstance(current_properties, dict):
        drift.append("properties")
    else:
        for key, value in desired_properties.items():
            if current_properties.get(key) != value:
                drift.append(f"properties.{key}")

    current_storage = current.get("storageConfigInfo")
    desired_storage = cast(dict[str, object], desired["storageConfigInfo"])
    if not isinstance(current_storage, dict):
        drift.append("storageConfigInfo")
    else:
        for key in (
            "storageType",
            "endpoint",
            "endpointInternal",
            "pathStyleAccess",
            "region",
            "stsUnavailable",
            "kmsUnavailable",
        ):
            if current_storage.get(key) != desired_storage[key]:
                drift.append(f"storageConfigInfo.{key}")
        current_locations = current_storage.get("allowedLocations")
        if not isinstance(current_locations, list) or set(current_locations) != set(
            cast(list[str], desired_storage["allowedLocations"])
        ):
            drift.append("storageConfigInfo.allowedLocations")
    return drift


def _catalog_object(current_payload: object) -> dict[str, Any]:
    if not isinstance(current_payload, dict):
        raise RuntimeError("Polaris catalog response is not a JSON object")
    current = current_payload.get("catalog", current_payload)
    if not isinstance(current, dict):
        raise RuntimeError("Polaris response does not contain a catalog object")
    return current


def _reconcile_existing_catalog(
    client: PolarisManagementClient,
    desired: dict[str, Any],
    current_payload: object,
) -> None:
    catalog_name = cast(str, desired["name"])
    for attempt in range(2):
        current = _catalog_object(current_payload)
        immutable_drift = [key for key in ("name", "type") if current.get(key) != desired[key]]
        if immutable_drift:
            raise RuntimeError(
                f"Existing Polaris catalog {catalog_name!r} has immutable contract drift "
                f"at: {', '.join(immutable_drift)}"
            )

        drift = _catalog_drift(current, desired)
        if not drift:
            logger.info("Polaris catalog %s already matches its contract", catalog_name)
            return

        entity_version = current.get("entityVersion")
        if not isinstance(entity_version, int) or isinstance(entity_version, bool):
            raise RuntimeError(
                f"Existing Polaris catalog {catalog_name!r} drifts at "
                f"{', '.join(drift)}, but its entityVersion is missing or invalid"
            )
        response = client.update_catalog(
            catalog_name,
            {
                "currentEntityVersion": entity_version,
                "properties": desired["properties"],
                "storageConfigInfo": desired["storageConfigInfo"],
            },
        )
        if response.status_code in (200, 204):
            logger.info(
                "Updated mutable Polaris catalog contract for %s at: %s",
                catalog_name,
                ", ".join(drift),
            )
            return
        if response.status_code != 409 or attempt == 1:
            response.raise_for_status()

        # Optimistic concurrency: reread once and either observe the desired state or
        # retry against the new entity version.
        response = client.get_catalog(catalog_name)
        response.raise_for_status()
        current_payload = response.json()


def ensure_catalog(client: PolarisManagementClient, desired: dict[str, Any]) -> None:
    catalog_name = cast(str, desired["name"])
    response = client.get_catalog(catalog_name)
    if response.status_code == 200:
        _reconcile_existing_catalog(client, desired, response.json())
        return
    if response.status_code != 404:
        response.raise_for_status()

    response = client.create_catalog(desired)
    if response.status_code in (200, 201):
        logger.info("Created Polaris catalog %s", catalog_name)
        return
    if response.status_code != 409:
        response.raise_for_status()
        return

    # A concurrent bootstrap may create the catalog between the GET and POST.
    response = client.get_catalog(catalog_name)
    response.raise_for_status()
    _reconcile_existing_catalog(client, desired, response.json())
