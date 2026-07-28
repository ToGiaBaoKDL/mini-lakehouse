"""Read-only validation for environment settings and declarative contracts."""

import json

from mini_lakehouse.config import get_settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.platform.catalog.layout import managed_tables, validate_runtime_contract


def main() -> None:
    settings = get_settings()
    contracts = load_contracts(settings.contracts_dir)
    validate_runtime_contract(settings, contracts)
    print(
        json.dumps(
            {
                "catalog": contracts.platform.catalog.name,
                "service_identities": len(contracts.access.service_identities),
                "catalog_roles": len(contracts.access.catalog_roles),
                "managed_namespaces": len(contracts.managed_namespaces()),
                "sources": len(contracts.sources),
                "curated_products": len(contracts.curated),
                "domains": len(contracts.domains),
                "processors": len(contracts.processors),
                "policies": len(contracts.policies),
                "managed_tables": sum(1 for _ in managed_tables(settings, contracts)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
