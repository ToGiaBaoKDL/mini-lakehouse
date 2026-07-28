from typing import Any, cast
from unittest.mock import create_autospec

import pytest
from pyiceberg.catalog import Catalog
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.transforms import HourTransform, IdentityTransform, MonthTransform, Transform
from pyiceberg.types import DateType, IcebergType, NestedField, TimestamptzType

from mini_lakehouse.contracts import TableIdentifier, load_contracts
from mini_lakehouse.contracts.maintenance import MaintenancePolicy, policy_content_json
from mini_lakehouse.platform.catalog.polaris import (
    PolarisPolicy,
    PolarisPolicyClient,
)
from mini_lakehouse.platform.maintenance import (
    build_maintenance_plan,
    maintenance_statements,
)
from mini_lakehouse.storage.iceberg import iceberg_metadata_retention_properties


def _target_applies(table: TableIdentifier, target_type: str, path: tuple[str, ...]) -> bool:
    if target_type == "table-like":
        return path == table.iceberg
    return table.namespace[: len(path)] == path


def _policies(table: TableIdentifier) -> list[PolarisPolicy]:
    return [
        PolarisPolicy.model_validate(
            {
                "name": spec.name,
                "policy-type": spec.policy_type,
                "description": spec.description,
                "content": policy_content_json(spec),
                "version": 1,
                "inheritable": True,
                "inherited": False,
                "namespace": list(spec.namespace),
            }
        )
        for spec in load_contracts().policies
        if any(_target_applies(table, target.type, target.path) for target in spec.targets)
    ]


def _policy_contracts() -> dict[tuple[tuple[str, ...], str], MaintenancePolicy]:
    return {(policy.namespace, policy.name): policy for policy in load_contracts().policies}


def _partitioned_table(
    field_name: str,
    field_type: IcebergType,
    transform: Transform[Any, Any],
    properties: dict[str, str] | None = None,
) -> Table:
    table = create_autospec(Table, instance=True)
    table.schema.return_value = Schema(
        NestedField(
            field_id=1,
            name=field_name,
            field_type=field_type,
            required=False,
        )
    )
    table.spec.return_value = PartitionSpec(
        PartitionField(
            source_id=1,
            field_id=1000,
            transform=transform,
            name=f"{field_name}_partition",
        )
    )
    table.properties = properties or {}
    return table


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


def test_contract_resolves_table_retention_before_the_storage_boundary() -> None:
    retention = load_contracts().maintenance.metadata_retention(
        TableIdentifier(("curated", "github"), "events")
    )

    assert retention.delete_after_commit is True
    assert retention.previous_versions_max == 30


def test_polaris_policies_compile_to_safe_trino_maintenance() -> None:
    table = TableIdentifier(("analytics", "engineering"), "fct_repository_activity_daily")

    assert maintenance_statements(
        table,
        _partitioned_table("activity_date", DateType(), MonthTransform()),
        "prod",
        _policies(table),
        _policy_contracts(),
    ) == (
        'ALTER TABLE "prod"."analytics.engineering"."fct_repository_activity_daily" '
        "SET PROPERTIES delete_after_commit_enabled = true, max_previous_versions = 30",
        'ALTER TABLE "prod"."analytics.engineering"."fct_repository_activity_daily" '
        "EXECUTE optimize(file_size_threshold => '128MB') WHERE \"activity_date\" "
        ">= CAST(date_trunc('month', current_date - INTERVAL '30' DAY) AS date)",
        'ALTER TABLE "prod"."analytics.engineering"."fct_repository_activity_daily" '
        "EXECUTE optimize_manifests",
        'ALTER TABLE "prod"."analytics.engineering"."fct_repository_activity_daily" '
        "EXECUTE expire_snapshots(retention_threshold => '14d')",
        'ALTER TABLE "prod"."analytics.engineering"."fct_repository_activity_daily" '
        "EXECUTE remove_orphan_files(retention_threshold => '30d')",
    )


def test_hour_partition_compaction_aligns_the_timestamp_boundary() -> None:
    table = TableIdentifier(("landing",), "github_archive_events_raw")

    statements = maintenance_statements(
        table,
        _partitioned_table("source_hour", TimestamptzType(), HourTransform()),
        "prod",
        _policies(table),
        _policy_contracts(),
    )

    assert statements[1] == (
        'ALTER TABLE "prod"."landing"."github_archive_events_raw" '
        "EXECUTE optimize(file_size_threshold => '128MB') WHERE \"source_hour\" "
        ">= date_trunc('hour', current_timestamp - INTERVAL '2' DAY)"
    )


def test_identity_partition_compaction_keeps_the_exact_date_boundary() -> None:
    table = TableIdentifier(("curated", "github"), "events")

    statements = maintenance_statements(
        table,
        _partitioned_table("event_date_utc", DateType(), IdentityTransform()),
        "prod",
        _policies(table),
        _policy_contracts(),
    )

    assert statements[1].endswith("WHERE \"event_date_utc\" >= current_date - INTERVAL '7' DAY")


def test_current_metadata_retention_does_not_generate_a_redundant_commit() -> None:
    table = TableIdentifier(("curated", "github"), "events")
    policy = next(
        spec
        for spec in load_contracts().policies
        if spec.policy_type == "system.metadata-compaction" and spec.namespace == ("curated",)
    )
    assert policy.retention is not None
    iceberg_table = _partitioned_table(
        "event_date_utc",
        DateType(),
        IdentityTransform(),
        iceberg_metadata_retention_properties(policy.retention),
    )

    statements = maintenance_statements(
        table,
        iceberg_table,
        "prod",
        _policies(table),
        _policy_contracts(),
    )

    assert not any(" SET PROPERTIES " in statement for statement in statements)


def test_duplicate_policy_type_is_rejected() -> None:
    table = TableIdentifier(("curated", "github"), "events")
    policies = _policies(table)

    with pytest.raises(ValueError, match="Multiple maintenance policies"):
        maintenance_statements(
            table,
            _partitioned_table("event_date_utc", DateType(), IdentityTransform()),
            "prod",
            [*policies, policies[0]],
            _policy_contracts(),
        )


def test_live_policy_content_must_match_the_yaml_contract() -> None:
    table = TableIdentifier(("curated", "github"), "events")
    policies = _policies(table)
    snapshot_policy = next(
        policy for policy in policies if policy.policy_type == "system.snapshot-expiry"
    )
    drifted = snapshot_policy.model_copy(
        update={
            "content": (
                '{"config":{"max_snapshot_age_days":90},"enable":true,"version":"2025-02-03"}'
            )
        }
    )

    with pytest.raises(ValueError, match="content drifted from its contract"):
        maintenance_statements(
            table,
            _partitioned_table("event_date_utc", DateType(), IdentityTransform()),
            "prod",
            [drifted if policy is snapshot_policy else policy for policy in policies],
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

    def load_table(self, identifier: tuple[str, ...]) -> Table:
        if identifier[0] == "analytics":
            return _partitioned_table("activity_date", DateType(), MonthTransform())
        return _partitioned_table("event_date_utc", DateType(), IdentityTransform())


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
    assert all(len(item.statements) == 5 for item in plan)
