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


def _target_applies(table: TableIdentifier, target_type: str, path: tuple[str, ...]) -> bool:
    if target_type == "table-like":
        return path == table.iceberg
    return table.namespace[: len(path)] == path


def _policies(table: TableIdentifier) -> list[PolarisPolicy]:
    return [
        PolarisPolicy.model_validate(
            {
                "name": spec.name,
                "type": spec.policy_type,
                "description": spec.description,
                "content": policy_content_json(spec),
                "version": 1,
                "inheritable": True,
                "namespace": spec.namespace,
            }
        )
        for spec in load_contracts().policies
        if any(_target_applies(table, target.type, target.path) for target in spec.targets)
    ]


def _policy_contracts() -> dict[tuple[tuple[str, ...], str], object]:
    return {(policy.namespace, policy.name): policy for policy in load_contracts().policies}


def test_maintenance_contract_is_isolated_by_lifecycle_tier() -> None:
    specs = load_contracts().policies

    assert {spec.policy_type for spec in specs} == {
        "system.data-compaction",
        "system.metadata-compaction",
        "system.snapshot-expiry",
        "system.orphan-file-removal",
    }
    assert len(specs) == 12
    assert {spec.namespace for spec in specs} == {
        ("landing",),
        ("curated",),
        ("analytics",),
    }
    assert all(
        all(not target.path or target.path[0] == spec.namespace[0] for target in spec.targets)
        for spec in specs
    )


def test_polaris_policies_compile_to_safe_trino_maintenance() -> None:
    table = TableIdentifier(("analytics", "engineering"), "fct_repository_activity_daily")

    assert maintenance_statements(
        table,
        "prod",
        _policies(table),
        _policy_contracts(),
    ) == (
        'ALTER TABLE "prod"."analytics.engineering"."fct_repository_activity_daily" '
        "EXECUTE optimize(file_size_threshold => '128MB') WHERE \"activity_date\" "
        ">= current_date - INTERVAL '30' DAY",
        'ALTER TABLE "prod"."analytics.engineering"."fct_repository_activity_daily" '
        "EXECUTE optimize_manifests",
        'ALTER TABLE "prod"."analytics.engineering"."fct_repository_activity_daily" '
        "EXECUTE expire_snapshots(retention_threshold => '14d')",
        'ALTER TABLE "prod"."analytics.engineering"."fct_repository_activity_daily" '
        "EXECUTE remove_orphan_files(retention_threshold => '30d')",
    )


def test_duplicate_policy_type_is_rejected() -> None:
    table = TableIdentifier(("curated", "github"), "events")
    policies = _policies(table)

    with pytest.raises(ValueError, match="Multiple maintenance policies"):
        maintenance_statements(
            table,
            "prod",
            [*policies, policies[0]],
            _policy_contracts(),
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
            ("curated", "github"): [("curated", "github", "events")],
            ("analytics", "engineering"): [
                ("analytics", "engineering", "fct_repository_activity_daily")
            ],
        }
        return tables.get(namespace, [])


class _PolicyClient:
    def applicable_policies(self, table: TableIdentifier) -> list[PolarisPolicy]:
        return _policies(table)


def test_maintenance_plan_discovers_nested_tables_without_an_allowlist() -> None:
    plan = build_maintenance_plan(
        cast(Catalog, _Catalog()),
        cast(PolarisPolicyClient, _PolicyClient()),
        "prod",
        load_contracts(),
    )

    assert [item.table for item in plan] == [
        '"prod"."analytics.engineering"."fct_repository_activity_daily"',
        '"prod"."curated.github"."events"',
    ]
    assert all(len(item.statements) == 4 for item in plan)
