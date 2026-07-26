"""Read-only validation for environment settings and declarative contracts."""

import json

from mini_lakehouse.config import get_settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.platform.runtime import validate_runtime_contract


def main() -> None:
    settings = get_settings()
    contracts = load_contracts(settings.contracts_dir)
    validate_runtime_contract(settings, contracts)
    print(
        json.dumps(
            {
                "catalog": contracts.catalog.catalog.name,
                "catalog_role_grants": len(contracts.catalog.catalog_role_grants),
                "managed_namespaces": len(contracts.managed_namespaces()),
                "sources": len(contracts.sources),
                "curated_products": len(contracts.curated_products),
                "domains": len(contracts.domains),
                "processors": len(contracts.processors),
                "policies": len(contracts.policies),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
