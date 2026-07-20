import logging
from collections.abc import Mapping
from time import sleep
from typing import Any

import requests
from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError

from mini_lakehouse.config import Settings, get_settings
from mini_lakehouse.logging import configure_logging
from mini_lakehouse.platform.polaris import (
    PolarisPolicyClient,
    create_retry_session,
    request_oauth_token,
)
from mini_lakehouse.platform.policies import maintenance_policy_contract
from mini_lakehouse.storage.iceberg import load_prod_catalog

logger = logging.getLogger(__name__)


def catalog_contract(settings: Settings) -> dict[str, Any]:
    storage = settings.storage
    external_endpoint = storage.endpoint_url or "https://s3.amazonaws.com"
    internal_endpoint = external_endpoint
    if settings.environment == "local":
        external_endpoint = "http://localhost:9000"
        internal_endpoint = storage.endpoint_url or "http://object-store:9000"
    return {
        "name": settings.polaris.catalog_name,
        "type": "INTERNAL",
        "properties": {
            "default-base-location": f"{storage.landing_uri}/_catalog",
            "polaris.config.namespace-custom-location.enabled": "true",
        },
        "storageConfigInfo": {
            "storageType": "S3",
            "endpoint": external_endpoint,
            "endpointInternal": internal_endpoint,
            "pathStyleAccess": settings.environment == "local",
            "region": storage.region,
            "stsUnavailable": settings.environment == "local",
            "kmsUnavailable": settings.environment == "local",
            "allowedLocations": [
                storage.landing_uri,
                storage.curated_uri,
                storage.analytics_uri,
            ],
        },
    }


def ensure_catalog(session: requests.Session, settings: Settings, token: str) -> None:
    response = session.post(
        f"{settings.polaris.management_uri.rstrip('/')}/catalogs",
        json=catalog_contract(settings),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Polaris-Realm": settings.polaris.realm,
        },
        timeout=15,
    )
    if response.status_code in (200, 201, 409):
        logger.info("Polaris catalog %s is ready", settings.polaris.catalog_name)
        return
    response.raise_for_status()


def namespace_contract(settings: Settings) -> Mapping[tuple[str, ...], dict[str, str]]:
    return {
        ("landing",): {
            "location": settings.storage.landing_uri,
            "owner": "data-platform",
            "data_tier": "landing",
        },
        ("landing", "api"): {"owner": "data-platform", "transport": "api"},
        ("landing", "api", "github_archive"): {
            "owner": "data-platform",
            "source_system": "github_archive",
        },
        ("curated",): {
            "location": settings.storage.curated_uri,
            "owner": "data-platform",
            "data_tier": "curated",
        },
        ("curated", "github"): {
            "owner": "data-platform",
            "data_product": "github",
        },
        ("curated", "github", "internal"): {
            "owner": "data-platform",
            "visibility": "private",
        },
        ("analytics",): {
            "location": settings.storage.analytics_uri,
            "owner": "analytics-platform",
            "data_tier": "analytics",
        },
        ("analytics", "engineering"): {
            "owner": "engineering-analytics",
            "business_domain": "engineering",
        },
    }


def ensure_namespaces(catalog: Catalog, settings: Settings) -> None:
    for namespace, properties in namespace_contract(settings).items():
        try:
            catalog.create_namespace(namespace, properties)
        except NamespaceAlreadyExistsError:
            catalog.update_namespace_properties(namespace, updates=properties)
        logger.info("Namespace %s is ready", ".".join(namespace))


def _load_catalog_with_retry(settings: Settings) -> Catalog:
    last_error: Exception | None = None
    for _ in range(12):
        try:
            catalog = load_prod_catalog(settings)
            catalog.list_namespaces()
            return catalog
        except Exception as error:
            last_error = error
            sleep(2)
    raise RuntimeError("Polaris catalog did not become readable") from last_error


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    session = create_retry_session()
    token = request_oauth_token(session, settings)
    ensure_catalog(session, settings, token)
    ensure_namespaces(_load_catalog_with_retry(settings), settings)
    policy_client = PolarisPolicyClient(session, settings, token)
    for policy in maintenance_policy_contract():
        policy_client.ensure_policy(policy)
        logger.info("Polaris policy %s is ready", policy.name)
    logger.info("Lakehouse catalog contract is ready")


if __name__ == "__main__":
    main()
