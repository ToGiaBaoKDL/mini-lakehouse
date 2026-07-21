from typing import cast

import pytest
from pyiceberg.catalog import Catalog

from mini_lakehouse.contracts import TableIdentifier, load_contracts
from mini_lakehouse.contracts.policies import policy_content_json
from mini_lakehouse.platform.maintenance import build_maintenance_plan
from mini_lakehouse.platform.polaris import PolarisPolicyClient
from mini_lakehouse.platform.policies import (
    PolarisPolicy,
    maintenance_statements,
)


def _policies() -> list[PolarisPolicy]:
    return [
        PolarisPolicy.model_validate(
            {
                "name": spec.name,
                "type": spec.policy_type,
                "description": spec.description,
                "content": policy_content_json(spec),
                "version": 1,
                "inheritable": True,
            }
        )
        for spec in load_contracts().policies
    ]


def test_maintenance_contract_is_inherited_from_data_tier_namespaces() -> None:
    specs = load_contracts().policies

    assert {spec.policy_type for spec in specs} == {
        "system.data-compaction",
        "system.metadata-compaction",
        "system.snapshot-expiry",
        "system.orphan-file-removal",
    }
    assert all(spec.namespace == ("curated",) for spec in specs)
    assert all(
        tuple(target.path for target in spec.targets)
        == (("landing",), ("curated",), ("analytics",))
        for spec in specs
    )


def test_polaris_policies_compile_to_safe_trino_maintenance() -> None:
    table = TableIdentifier(("analytics", "engineering"), "events_daily")

    assert maintenance_statements(table, "prod", _policies()) == (
        'ALTER TABLE "prod"."analytics.engineering"."events_daily" '
        "EXECUTE optimize(file_size_threshold => '128MB')",
        'ALTER TABLE "prod"."analytics.engineering"."events_daily" EXECUTE optimize_manifests',
        'ALTER TABLE "prod"."analytics.engineering"."events_daily" '
        "EXECUTE expire_snapshots(retention_threshold => '7d')",
        'ALTER TABLE "prod"."analytics.engineering"."events_daily" '
        "EXECUTE remove_orphan_files(retention_threshold => '30d')",
    )


def test_duplicate_policy_type_is_rejected() -> None:
    policies = _policies()

    with pytest.raises(ValueError, match="Multiple maintenance policies"):
        maintenance_statements(
            TableIdentifier(("curated", "github"), "events"),
            "prod",
            [*policies, policies[0]],
        )


class _Catalog:
    def list_namespaces(self, namespace: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
        namespaces: dict[tuple[str, ...], list[tuple[str, ...]]] = {
            (): [("curated",), ("analytics",)],
            ("curated",): [("curated", "github")],
            ("analytics",): [("analytics", "engineering")],
        }
        return namespaces.get(namespace, [])

    def list_tables(self, namespace: tuple[str, ...]) -> list[tuple[str, ...]]:
        tables: dict[tuple[str, ...], list[tuple[str, ...]]] = {
            ("curated", "github"): [("curated", "github", "github_events")],
            ("analytics", "engineering"): [("analytics", "engineering", "events_daily")],
        }
        return tables.get(namespace, [])


class _PolicyClient:
    def applicable_policies(self, _table: TableIdentifier) -> list[PolarisPolicy]:
        return _policies()


def test_maintenance_plan_discovers_nested_tables_without_an_allowlist() -> None:
    plan = build_maintenance_plan(
        cast(Catalog, _Catalog()),
        cast(PolarisPolicyClient, _PolicyClient()),
        "prod",
    )

    assert [item.table for item in plan] == [
        '"prod"."analytics.engineering"."events_daily"',
        '"prod"."curated.github"."github_events"',
    ]
    assert all(len(item.statements) == 4 for item in plan)
