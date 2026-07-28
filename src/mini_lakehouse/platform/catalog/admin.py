import argparse
import json
import logging
from collections.abc import Sequence

from apache_polaris.sdk.management.api.polaris_default_api import PolarisDefaultApi

from mini_lakehouse.config import Settings, get_settings
from mini_lakehouse.contracts import PlatformContracts, load_contracts
from mini_lakehouse.logging import configure_logging
from mini_lakehouse.platform.catalog.access import (
    bootstrap_access,
    rotate_credentials,
    validate_access,
)
from mini_lakehouse.platform.catalog.catalogs import (
    bootstrap_catalog,
    validate_catalog,
)
from mini_lakehouse.platform.catalog.layout import validate_runtime_contract
from mini_lakehouse.platform.catalog.polaris import PolarisClients
from mini_lakehouse.platform.catalog.policies import (
    PolarisPolicyClient,
    bootstrap_policies,
    validate_policies,
)
from mini_lakehouse.platform.catalog.tables import bootstrap_iceberg, validate_iceberg

logger = logging.getLogger(__name__)


def require_valid(errors: tuple[str, ...]) -> None:
    if errors:
        raise RuntimeError("Platform validation failed:\n- " + "\n- ".join(errors))


def validation_payload(errors: tuple[str, ...]) -> dict[str, object]:
    return {"valid": not errors, "errors": list(errors)}


def validate_platform(
    settings: Settings,
    contracts: PlatformContracts,
    management: PolarisDefaultApi,
    policies: PolarisPolicyClient,
) -> tuple[str, ...]:
    catalog_errors = validate_catalog(management, settings, contracts)
    if catalog_errors == (f"catalog:{contracts.platform.catalog.name}:missing",):
        return catalog_errors

    iceberg_errors, namespaces, tables = validate_iceberg(settings, contracts)
    errors = [
        *catalog_errors,
        *validate_access(management, contracts),
        *iceberg_errors,
        *validate_policies(policies, contracts, namespaces, tables),
    ]
    return tuple(sorted(errors))


def bootstrap_platform(
    settings: Settings,
    contracts: PlatformContracts,
    clients: PolarisClients,
) -> tuple[str, ...]:
    validate_runtime_contract(settings, contracts)
    bootstrap_catalog(clients.management, settings, contracts)
    bootstrap_iceberg(settings, contracts)
    bootstrap_access(clients.management, settings, contracts)
    bootstrap_policies(clients.policies, contracts)
    errors = validate_platform(
        settings,
        contracts,
        clients.management,
        clients.policies,
    )
    require_valid(errors)
    return errors


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap or validate the contract-managed lakehouse platform."
    )
    parser.add_argument(
        "operation",
        choices=("bootstrap", "validate", "rotate-credentials"),
    )
    parser.add_argument(
        "identities",
        nargs="*",
        help="Service identities to rotate; defaults to every contract identity.",
    )
    parsed = parser.parse_args(arguments)

    settings = get_settings()
    configure_logging(settings.log_level)
    settings.platform_admin.require_capability()
    contracts = load_contracts(settings.contracts_dir)
    validate_runtime_contract(settings, contracts)
    with PolarisClients(settings) as clients:
        if parsed.operation == "bootstrap":
            errors = bootstrap_platform(settings, contracts, clients)
            logger.info("Platform bootstrap completed without drift")
        elif parsed.operation == "rotate-credentials":
            rotate_credentials(
                clients.management,
                settings,
                contracts,
                parsed.identities,
            )
            errors = validate_platform(
                settings,
                contracts,
                clients.management,
                clients.policies,
            )
            require_valid(errors)
            logger.info("Polaris service credentials rotated")
        else:
            errors = validate_platform(
                settings,
                contracts,
                clients.management,
                clients.policies,
            )
            require_valid(errors)
    print(json.dumps(validation_payload(errors), indent=2))


if __name__ == "__main__":
    main()
