import logging

from mini_lakehouse.config import get_settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.logging import configure_logging
from mini_lakehouse.platform.access import ensure_catalog_role_grants
from mini_lakehouse.platform.catalog import catalog_contract, ensure_catalog
from mini_lakehouse.platform.namespaces import ensure_namespaces, load_catalog_with_retry
from mini_lakehouse.platform.polaris import (
    PolarisManagementClient,
    PolarisPolicyClient,
    create_retry_session,
    request_oauth_token,
)
from mini_lakehouse.platform.runtime import validate_runtime_contract

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    contracts = load_contracts(settings.contracts_dir)
    validate_runtime_contract(settings, contracts)
    session = create_retry_session()
    token = request_oauth_token(session, settings)
    management = PolarisManagementClient(session, settings, token)
    ensure_catalog(management, catalog_contract(settings, contracts))
    ensure_catalog_role_grants(management, contracts)
    ensure_namespaces(load_catalog_with_retry(settings), settings, contracts)
    policy_client = PolarisPolicyClient(session, settings, token)
    for policy in contracts.policies:
        result = policy_client.reconcile_policy(policy)
        logger.info(
            "Polaris policy %s: %s; ensured %d mappings",
            result.policy,
            result.action,
            result.ensured_mappings,
        )
    logger.info("Lakehouse catalog contract is ready")


if __name__ == "__main__":
    main()
