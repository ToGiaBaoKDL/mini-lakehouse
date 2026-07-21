import logging

from mini_lakehouse.contracts import PlatformContracts
from mini_lakehouse.platform.polaris import PolarisManagementClient

logger = logging.getLogger(__name__)


def ensure_catalog_role_grants(
    client: PolarisManagementClient,
    contracts: PlatformContracts,
) -> int:
    catalog_name = contracts.catalog.catalog.name
    granted = 0
    for role in contracts.catalog.catalog_role_grants:
        response = client.get_catalog_role_grants(catalog_name, role.catalog_role)
        response.raise_for_status()
        payload = response.json()
        current_grants = payload.get("grants")
        if not isinstance(current_grants, list):
            raise RuntimeError(f"Polaris did not return grants for role {role.catalog_role!r}")
        current_privileges = {
            grant.get("privilege")
            for grant in current_grants
            if isinstance(grant, dict) and grant.get("type") == "catalog"
        }
        for privilege in role.privileges:
            if privilege in current_privileges:
                continue
            response = client.grant_catalog_privilege(
                catalog_name,
                role.catalog_role,
                privilege,
            )
            response.raise_for_status()
            granted += 1
        logger.info("Catalog role %s matches its privilege contract", role.catalog_role)
    return granted
