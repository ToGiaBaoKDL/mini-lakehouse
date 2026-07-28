from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from pyiceberg.catalog import Catalog
from pyiceberg.table import Table
from pyiceberg.transforms import (
    DayTransform,
    HourTransform,
    IdentityTransform,
    MonthTransform,
    YearTransform,
)
from pyiceberg.types import DateType, TimestamptzType

from mini_lakehouse.contracts import PlatformContracts, TableIdentifier
from mini_lakehouse.contracts.maintenance import (
    MAINTENANCE_POLICY_TYPES,
    DataCompactionPolicyContent,
    MaintenancePolicy,
    OrphanFileRemovalPolicyContent,
    PolicyType,
    SnapshotExpiryPolicyContent,
    parse_policy_content,
)
from mini_lakehouse.platform.catalog.polaris import (
    PolarisPolicy,
    PolarisPolicyClient,
    policy_content_object,
)
from mini_lakehouse.storage.iceberg import (
    discover_tables,
    metadata_retention_is_current,
    trino_metadata_retention_properties,
)


@dataclass(frozen=True, slots=True)
class MaintenancePlanItem:
    table: str
    statements: tuple[str, ...]


def _data_size(value: int) -> str:
    mebibyte = 1024 * 1024
    if value % mebibyte == 0:
        return f"{value // mebibyte}MB"
    return f"{value}B"


def _optimize_boundary(table: Table, partition_field: str, lookback_days: int) -> str:
    source = table.schema().find_field(partition_field)
    partition_fields = [
        field for field in table.spec().fields if field.source_id == source.field_id
    ]
    if len(partition_fields) != 1:
        raise ValueError(
            f"Bounded optimize field {partition_field!r} must have exactly one active "
            f"partition transform; found {len(partition_fields)}"
        )

    transform = partition_fields[0].transform
    if isinstance(source.field_type, DateType):
        boundary = f"current_date - INTERVAL '{lookback_days}' DAY"
        if isinstance(transform, (IdentityTransform, DayTransform)):
            return boundary
        if isinstance(transform, MonthTransform):
            return f"CAST(date_trunc('month', {boundary}) AS date)"
        if isinstance(transform, YearTransform):
            return f"CAST(date_trunc('year', {boundary}) AS date)"
    elif isinstance(source.field_type, TimestamptzType):
        boundary = f"current_timestamp - INTERVAL '{lookback_days}' DAY"
        if isinstance(transform, IdentityTransform):
            return boundary
        if isinstance(transform, HourTransform):
            return f"date_trunc('hour', {boundary})"
        if isinstance(transform, DayTransform):
            return f"date_trunc('day', {boundary})"
        if isinstance(transform, MonthTransform):
            return f"date_trunc('month', {boundary})"
        if isinstance(transform, YearTransform):
            return f"date_trunc('year', {boundary})"

    raise ValueError(
        f"Bounded optimize does not support {transform!s} over "
        f"{source.field_type!s} field {partition_field!r}"
    )


def maintenance_statements(
    table: TableIdentifier,
    iceberg_table: Table,
    catalog: str,
    policies: list[PolarisPolicy],
    policy_contracts: Mapping[tuple[tuple[str, ...], str], MaintenancePolicy],
) -> tuple[str, ...]:
    applicable = {
        policy.policy_type: policy
        for policy in policies
        if policy.policy_type in MAINTENANCE_POLICY_TYPES
    }
    if len(applicable) != sum(
        policy.policy_type in MAINTENANCE_POLICY_TYPES for policy in policies
    ):
        raise ValueError(f"Multiple maintenance policies of the same type apply to {table.iceberg}")

    resolved: dict[PolicyType, MaintenancePolicy] = {}
    for policy_type in MAINTENANCE_POLICY_TYPES:
        policy = applicable.get(policy_type)
        if policy is None:
            continue
        contract = policy_contracts.get((tuple(policy.namespace), policy.name))
        if contract is None or contract.policy_type != policy_type:
            raise ValueError(f"Maintenance policy {policy.name!r} has no matching contract")
        live_content = parse_policy_content(
            policy_type,
            policy_content_object(policy),
        )
        if live_content != contract.content:
            raise ValueError(
                f"Maintenance policy {policy.name!r} content drifted from its contract"
            )
        resolved[policy_type] = contract

    relation = table.trino(catalog)
    statements: list[str] = []
    metadata_contract = resolved.get("system.metadata-compaction")
    if metadata_contract is not None:
        if metadata_contract.retention is None:
            raise ValueError(
                f"Metadata compaction policy {metadata_contract.name!r} "
                "has no typed retention contract"
            )
        if not metadata_retention_is_current(
            iceberg_table.properties,
            metadata_contract.retention,
        ):
            assignments = ", ".join(
                f"{name} = {value}"
                for name, value in trino_metadata_retention_properties(
                    metadata_contract.retention
                ).items()
            )
            statements.append(f"ALTER TABLE {relation} SET PROPERTIES {assignments}")

    for policy_type in MAINTENANCE_POLICY_TYPES:
        contract = resolved.get(policy_type)
        if contract is None:
            continue
        content = contract.content
        if policy_type == "system.data-compaction":
            if not isinstance(content, DataCompactionPolicyContent):
                raise AssertionError("Unexpected data compaction policy content")
            if content.enable:
                if contract.execution is None:
                    raise ValueError(
                        f"Data compaction policy {contract.name!r} has no typed execution contract"
                    )
                boundary = _optimize_boundary(
                    iceberg_table,
                    contract.execution.partition_field,
                    contract.execution.lookback_days,
                )
                statements.append(
                    f"ALTER TABLE {relation} EXECUTE optimize(file_size_threshold => "
                    f"'{_data_size(content.config.target_file_size_bytes)}') WHERE "
                    f'"{contract.execution.partition_field}" >= {boundary}'
                )
        elif policy_type == "system.metadata-compaction":
            if content.enable:
                statements.append(f"ALTER TABLE {relation} EXECUTE optimize_manifests")
        elif policy_type == "system.snapshot-expiry":
            if not isinstance(content, SnapshotExpiryPolicyContent):
                raise AssertionError("Unexpected snapshot expiry policy content")
            if content.enable:
                statements.append(
                    f"ALTER TABLE {relation} EXECUTE expire_snapshots("
                    f"retention_threshold => '{content.config.max_snapshot_age_days}d')"
                )
        elif policy_type == "system.orphan-file-removal":
            if not isinstance(content, OrphanFileRemovalPolicyContent):
                raise AssertionError("Unexpected orphan removal policy content")
            if content.enable:
                statements.append(
                    f"ALTER TABLE {relation} EXECUTE remove_orphan_files("
                    f"retention_threshold => '{content.max_orphan_file_age_in_days}d')"
                )
    return tuple(statements)


def collect_maintenance_results(
    pending: Sequence[tuple[MaintenancePlanItem, Callable[..., int]]],
) -> tuple[int, int, list[str]]:
    """Resolve a submitted batch without abandoning successful sibling tables."""
    completed = 0
    statements = 0
    failures: list[str] = []
    for plan, result in pending:
        try:
            statements += result()
            completed += 1
        except Exception as error:
            failures.append(f"{plan.table}: {error}")
    return completed, statements, failures


def build_maintenance_plan(
    catalog: Catalog,
    policy_client: PolarisPolicyClient,
    trino_catalog: str,
    contracts: PlatformContracts,
) -> list[MaintenancePlanItem]:
    policy_contracts = {(policy.namespace, policy.name): policy for policy in contracts.policies}
    plan: list[MaintenancePlanItem] = []
    for table in sorted(discover_tables(catalog), key=lambda item: item.iceberg):
        iceberg_table = catalog.load_table(table.iceberg)
        statements = maintenance_statements(
            table,
            iceberg_table,
            trino_catalog,
            policy_client.applicable_policies(table),
            policy_contracts,
        )
        if statements:
            plan.append(
                MaintenancePlanItem(
                    table=table.trino(trino_catalog),
                    statements=statements,
                )
            )
    return plan
