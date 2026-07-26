import json
from collections.abc import Mapping
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from pyiceberg.table import Table
from pyiceberg.transforms import (
    DayTransform,
    HourTransform,
    IdentityTransform,
    MonthTransform,
    YearTransform,
)
from pyiceberg.types import DateType, TimestamptzType

from mini_lakehouse.contracts import TableIdentifier
from mini_lakehouse.contracts.policies import (
    DataCompactionContent,
    DataCompactionPolicyContract,
    MetadataCompactionContent,
    MetadataCompactionPolicyContract,
    OrphanFileRemovalContent,
    SnapshotExpiryContent,
)
from mini_lakehouse.storage.iceberg import (
    metadata_retention_is_current,
    trino_metadata_retention_properties,
)

MAINTENANCE_POLICY_TYPES = (
    "system.data-compaction",
    "system.metadata-compaction",
    "system.snapshot-expiry",
    "system.orphan-file-removal",
)


class PolarisPolicy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    policy_type: str = Field(validation_alias=AliasChoices("policy-type", "type"))
    description: str = ""
    content: str | dict[str, Any]
    version: int = Field(default=0, ge=0)
    inheritable: bool = False
    inherited: bool = False
    namespace: tuple[str, ...] = ()

    @field_validator("content", mode="before")
    @classmethod
    def validate_content(cls, value: object) -> str | dict[str, Any]:
        if isinstance(value, (str, dict)):
            return value
        raise ValueError("Policy content must be a JSON string or object")

    def content_object(self) -> dict[str, Any]:
        value = json.loads(self.content) if isinstance(self.content, str) else self.content
        if not isinstance(value, dict):
            raise ValueError(f"Policy {self.name!r} content must be a JSON object")
        return value

    def canonical_content(self) -> str:
        return json.dumps(self.content_object(), sort_keys=True, separators=(",", ":"))


class ApplicablePoliciesResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    applicable_policies: list[PolarisPolicy] = Field(
        default_factory=list,
        validation_alias=AliasChoices("applicable-policies", "policies"),
    )
    next_page_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("next-page-token", "nextPageToken", "next_page_token"),
    )


class PolicyIdentifier(BaseModel):
    model_config = ConfigDict(extra="ignore")

    namespace: tuple[str, ...]
    name: str


class ListPoliciesResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    identifiers: list[PolicyIdentifier] = Field(default_factory=list)
    next_page_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("next-page-token", "nextPageToken", "next_page_token"),
    )


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
    policy_contracts: Mapping[tuple[tuple[str, ...], str], object] | None = None,
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
        if not isinstance(contract, MetadataCompactionPolicyContract):
            raise ValueError(
                f"Metadata compaction policy {metadata_policy.name!r} "
                "has no typed retention contract"
            )
        if not metadata_retention_is_current(
            iceberg_table.properties,
            contract.retention,
        ):
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
            parsed = DataCompactionContent.model_validate(content)
            if parsed.enable:
                threshold = _data_size(parsed.config.target_file_size_bytes)
                contract = (policy_contracts or {}).get((policy.namespace, policy.name))
                if not isinstance(contract, DataCompactionPolicyContract):
                    raise ValueError(
                        f"Data compaction policy {policy.name!r} has no typed execution contract"
                    )
                execution = contract.execution
                boundary = _optimize_boundary(
                    iceberg_table,
                    execution.partition_field,
                    execution.lookback_days,
                )
                statements.append(
                    f"ALTER TABLE {relation} EXECUTE optimize(file_size_threshold => "
                    f"'{threshold}') WHERE \"{execution.partition_field}\" >= {boundary}"
                )
        elif policy_type == "system.metadata-compaction":
            parsed = MetadataCompactionContent.model_validate(content)
            if parsed.enable:
                statements.append(f"ALTER TABLE {relation} EXECUTE optimize_manifests")
        elif policy_type == "system.snapshot-expiry":
            parsed = SnapshotExpiryContent.model_validate(content)
            if parsed.enable:
                statements.append(
                    f"ALTER TABLE {relation} EXECUTE expire_snapshots("
                    f"retention_threshold => '{parsed.config.max_snapshot_age_days}d')"
                )
        elif policy_type == "system.orphan-file-removal":
            parsed = OrphanFileRemovalContent.model_validate(content)
            if parsed.enable:
                statements.append(
                    f"ALTER TABLE {relation} EXECUTE remove_orphan_files("
                    f"retention_threshold => '{parsed.max_orphan_file_age_in_days}d')"
                )
    return tuple(statements)
