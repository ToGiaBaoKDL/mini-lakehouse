"""Review or explicitly apply deletion of stale repository-managed Polaris policies."""

import argparse
import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from mini_lakehouse.config import get_settings
from mini_lakehouse.contracts import PlatformContracts, load_contracts
from mini_lakehouse.logging import configure_logging
from mini_lakehouse.platform.catalog.layout import validate_runtime_contract
from mini_lakehouse.platform.catalog.polaris import PolarisClients
from mini_lakehouse.platform.catalog.policies import PolarisPolicyClient

logger = logging.getLogger(__name__)
MANAGED_POLICY_PREFIX = "mlh-"


@dataclass(frozen=True, slots=True)
class PolicyPruneItem:
    namespace: tuple[str, ...]
    name: str


def build_policy_prune_plan(
    client: PolarisPolicyClient,
    contracts: PlatformContracts,
) -> tuple[PolicyPruneItem, ...]:
    desired = {(policy.namespace, policy.name) for policy in contracts.policies}
    stale: set[PolicyPruneItem] = set()
    for namespace in (item.path for item in contracts.managed_namespaces()):
        for identifier in client.list_policies(namespace):
            key = (tuple(identifier.namespace), identifier.name)
            if identifier.name.startswith(MANAGED_POLICY_PREFIX) and key not in desired:
                stale.add(PolicyPruneItem(*key))
    return tuple(sorted(stale, key=lambda item: (item.namespace, item.name)))


def apply_policy_prune_plan(
    client: PolarisPolicyClient,
    plan: tuple[PolicyPruneItem, ...],
) -> None:
    for item in plan:
        client.delete_policy(item.namespace, item.name)


def plan_payload(plan: tuple[PolicyPruneItem, ...]) -> dict[str, object]:
    policies = [{"namespace": list(item.namespace), "name": item.name} for item in plan]
    encoded = json.dumps(
        policies,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return {
        "plan_sha256": hashlib.sha256(encoded).hexdigest(),
        "policies": policies,
    }


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Plan stale managed-policy removal or apply one exact reviewed plan."
    )
    parser.add_argument(
        "--apply-plan-sha256",
        help="Delete only when the current plan matches this reviewed SHA-256.",
    )
    parsed = parser.parse_args(arguments)

    settings = get_settings()
    configure_logging(settings.log_level)
    settings.platform_admin.require_capability()
    contracts = load_contracts(settings.contracts_dir)
    validate_runtime_contract(settings, contracts)
    with PolarisClients(settings) as clients:
        plan = build_policy_prune_plan(clients.policies, contracts)
        payload = plan_payload(plan)
        print(json.dumps(payload, indent=2))
        expected_sha256 = parsed.apply_plan_sha256
        if expected_sha256 is not None:
            if expected_sha256 != payload["plan_sha256"]:
                raise RuntimeError(
                    "Polaris policy prune plan changed after review; generate and review a new plan"
                )
            if plan:
                apply_policy_prune_plan(clients.policies, plan)
                logger.info("Pruned %d stale managed Polaris policies", len(plan))
            else:
                logger.info("The reviewed Polaris policy prune plan is empty")


if __name__ == "__main__":
    main()
