"""Read-only validation for environment settings and declarative contracts."""

import json

from mini_lakehouse.config import get_settings
from mini_lakehouse.contracts import load_contracts
from mini_lakehouse.platform.desired_state import compile_desired_state


def main() -> None:
    settings = get_settings()
    contracts = load_contracts(settings.contracts_dir)
    state = compile_desired_state(settings, contracts)
    print(
        json.dumps(
            {
                "catalog": contracts.platform.catalog.name,
                "contract_digest": state.contract_digest,
                "catalog_role_grants": len(contracts.access.catalog_role_grants),
                "managed_namespaces": len(contracts.managed_namespaces()),
                "sources": len(contracts.sources),
                "curated_products": len(contracts.curated),
                "domains": len(contracts.domains),
                "processors": len(contracts.processors),
                "policies": len(contracts.policies),
                "managed_tables": len(state.managed_tables),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
