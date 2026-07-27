from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, field_validator
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
    POLARIS_POLICY_CONTENT_VERSION,
    MaintenancePolicy,
)
from mini_lakehouse.platform.polaris import PolarisPolicy, PolarisPolicyClient
from mini_lakehouse.storage.iceberg import (
    discover_tables,
    metadata_retention_is_current,
    trino_metadata_retention_properties,
)

MAINTENANCE_POLICY_TYPES = (
    "system.data-compaction",
    "system.metadata-compaction",
    "system.snapshot-expiry",
    "system.orphan-file-removal",
)


class _PolicyContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    enable: bool

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if value != POLARIS_POLICY_CONTENT_VERSION:
            raise ValueError(
                f"Expected Polaris policy content version {POLARIS_POLICY_CONTENT_VERSION!r}"
            )
        return value


class _DataCompactionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_file_size_bytes: int = Field(ge=1024 * 1024)


class _DataCompactionContent(_PolicyContent):
    config: _DataCompactionConfig


class _SnapshotExpiryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_snapshot_age_days: int = Field(ge=7)


class _SnapshotExpiryContent(_PolicyContent):
    config: _SnapshotExpiryConfig


class _OrphanFileRemovalContent(_PolicyContent):
    max_orphan_file_age_in_days: int = Field(ge=7)


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
    policy_contracts: Mapping[tuple[tuple[str, ...], str], MaintenancePolicy] | None = None,
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

    relation = table.trino(catalog)
    statements: list[str] = []
    metadata_policy = applicable.get("system.metadata-compaction")
    if metadata_policy is not None:
        contract = (policy_contracts or {}).get((metadata_policy.namespace, metadata_policy.name))
        if (
            contract is None
            or contract.policy_type != "system.metadata-compaction"
            or contract.retention is None
        ):
            raise ValueError(
                f"Metadata compaction policy {metadata_policy.name!r} "
                "has no typed retention contract"
            )
        if not metadata_retention_is_current(iceberg_table.properties, contract.retention):
            assignments = ", ".join(
                f"{name} = {value}"
                for name, value in trino_metadata_retention_properties(contract.retention).items()
            )
            statements.append(f"ALTER TABLE {relation} SET PROPERTIES {assignments}")

    for policy_type in MAINTENANCE_POLICY_TYPES:
        policy = applicable.get(policy_type)
        if policy is None:
            continue
        content = policy.content_object()
        if policy_type == "system.data-compaction":
            parsed = _DataCompactionContent.model_validate(content)
            if parsed.enable:
                contract = (policy_contracts or {}).get((policy.namespace, policy.name))
                if (
                    contract is None
                    or contract.policy_type != "system.data-compaction"
                    or contract.execution is None
                ):
                    raise ValueError(
                        f"Data compaction policy {policy.name!r} has no typed execution contract"
                    )
                boundary = _optimize_boundary(
                    iceberg_table,
                    contract.execution.partition_field,
                    contract.execution.lookback_days,
                )
                statements.append(
                    f"ALTER TABLE {relation} EXECUTE optimize(file_size_threshold => "
                    f"'{_data_size(parsed.config.target_file_size_bytes)}') WHERE "
                    f'"{contract.execution.partition_field}" >= {boundary}'
                )
        elif policy_type == "system.metadata-compaction":
            if _PolicyContent.model_validate(content).enable:
                statements.append(f"ALTER TABLE {relation} EXECUTE optimize_manifests")
        elif policy_type == "system.snapshot-expiry":
            parsed = _SnapshotExpiryContent.model_validate(content)
            if parsed.enable:
                statements.append(
                    f"ALTER TABLE {relation} EXECUTE expire_snapshots("
                    f"retention_threshold => '{parsed.config.max_snapshot_age_days}d')"
                )
        elif policy_type == "system.orphan-file-removal":
            parsed = _OrphanFileRemovalContent.model_validate(content)
            if parsed.enable:
                statements.append(
                    f"ALTER TABLE {relation} EXECUTE remove_orphan_files("
                    f"retention_threshold => '{parsed.max_orphan_file_age_in_days}d')"
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
