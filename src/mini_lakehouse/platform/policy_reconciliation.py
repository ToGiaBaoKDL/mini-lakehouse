"""Desired-state planning for removing stale mini-lakehouse Polaris policies."""

from dataclasses import dataclass

from mini_lakehouse.contracts import PlatformContracts
from mini_lakehouse.platform.polaris import (
    PolarisPolicyClient,
    PolicyReconcileResult,
)

MANAGED_POLICY_PREFIX = "mlh-"


@dataclass(frozen=True, slots=True)
class PolicyPruneItem:
    namespace: tuple[str, ...]
    name: str


@dataclass(frozen=True, slots=True)
class PolicyReconcileSummary:
    results: tuple[PolicyReconcileResult, ...]

    @property
    def ensured_mappings(self) -> int:
        return sum(result.ensured_mappings for result in self.results)

    @property
    def pending_mappings(self) -> int:
        return sum(result.pending_mappings for result in self.results)


def reconcile_policies(
    client: PolarisPolicyClient,
    contracts: PlatformContracts,
) -> PolicyReconcileSummary:
    return PolicyReconcileSummary(
        tuple(client.reconcile_policy(policy) for policy in contracts.policies)
    )


def build_policy_prune_plan(
    client: PolarisPolicyClient,
    contracts: PlatformContracts,
) -> tuple[PolicyPruneItem, ...]:
    desired = {(policy.namespace, policy.name) for policy in contracts.policies}
    stale: set[PolicyPruneItem] = set()
    for namespace in (item.path for item in contracts.catalog.namespaces):
        for identifier in client.list_policies(namespace):
            key = (identifier.namespace, identifier.name)
            if identifier.name.startswith(MANAGED_POLICY_PREFIX) and key not in desired:
                stale.add(PolicyPruneItem(*key))
    return tuple(sorted(stale, key=lambda item: (item.namespace, item.name)))


def apply_policy_prune_plan(
    client: PolarisPolicyClient,
    plan: tuple[PolicyPruneItem, ...],
) -> None:
    for item in plan:
        client.delete_policy(item.namespace, item.name)
