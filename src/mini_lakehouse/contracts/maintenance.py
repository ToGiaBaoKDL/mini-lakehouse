import json
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import Field, model_validator

from mini_lakehouse.contracts.base import (
    LIFECYCLE_TIERS,
    ContractModel,
    ContractName,
    Identifier,
    NamespacePath,
    StorageTier,
)
from mini_lakehouse.contracts.tables import TableIdentifier

type PolicyType = Literal[
    "system.data-compaction",
    "system.metadata-compaction",
    "system.snapshot-expiry",
    "system.orphan-file-removal",
]
MAINTENANCE_POLICY_TYPES: tuple[PolicyType, ...] = (
    "system.data-compaction",
    "system.metadata-compaction",
    "system.snapshot-expiry",
    "system.orphan-file-removal",
)
POLARIS_POLICY_CONTENT_VERSION = "2025-02-03"


class MetadataRetentionContract(ContractModel):
    delete_after_commit: bool
    previous_versions_max: int = Field(ge=1, le=100)


class PolicyTargetContract(ContractModel):
    type: Literal["namespace", "table-like"]
    path: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_path(self) -> Self:
        if self.type == "table-like" and len(self.path) < 2:
            raise ValueError("Table-like policy targets require a namespace and table")
        return self


class OptimizationContract(ContractModel):
    name: ContractName
    target: PolicyTargetContract
    partition_field: Identifier
    lookback_days: int = Field(ge=1, le=365)
    target_file_size_bytes: int = Field(ge=1024 * 1024)


class MaintenancePolicyContent(ContractModel):
    version: Literal["2025-02-03"] = POLARIS_POLICY_CONTENT_VERSION
    enable: bool = True


class DataCompactionConfig(ContractModel):
    target_file_size_bytes: int = Field(ge=1024 * 1024)


class DataCompactionPolicyContent(MaintenancePolicyContent):
    config: DataCompactionConfig


class SnapshotExpiryConfig(ContractModel):
    max_snapshot_age_days: int = Field(ge=7)


class SnapshotExpiryPolicyContent(MaintenancePolicyContent):
    config: SnapshotExpiryConfig


class OrphanFileRemovalPolicyContent(MaintenancePolicyContent):
    max_orphan_file_age_in_days: int = Field(ge=7)


type TypedMaintenancePolicyContent = (
    MaintenancePolicyContent
    | DataCompactionPolicyContent
    | SnapshotExpiryPolicyContent
    | OrphanFileRemovalPolicyContent
)


def parse_policy_content(
    policy_type: PolicyType,
    content: object,
) -> TypedMaintenancePolicyContent:
    if policy_type == "system.data-compaction":
        return DataCompactionPolicyContent.model_validate(content)
    if policy_type == "system.snapshot-expiry":
        return SnapshotExpiryPolicyContent.model_validate(content)
    if policy_type == "system.orphan-file-removal":
        return OrphanFileRemovalPolicyContent.model_validate(content)
    return MaintenancePolicyContent.model_validate(content)


@dataclass(frozen=True, slots=True)
class MaintenancePolicy:
    name: str
    namespace: NamespacePath
    owner: str
    policy_type: PolicyType
    description: str
    content: TypedMaintenancePolicyContent
    targets: tuple[PolicyTargetContract, ...]
    retention: MetadataRetentionContract | None = None
    execution: OptimizationContract | None = None


def policy_content_json(policy: MaintenancePolicy) -> str:
    return json.dumps(
        policy.content.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )


class TierMaintenanceContract(ContractModel):
    tier: StorageTier
    owner: ContractName
    metadata_retention: MetadataRetentionContract
    snapshot_max_age_days: int = Field(ge=7)
    orphan_file_max_age_days: int = Field(ge=7)
    optimizations: tuple[OptimizationContract, ...] = ()

    @model_validator(mode="after")
    def validate_tier_targets(self) -> Self:
        names = [optimization.name for optimization in self.optimizations]
        if len(names) != len(set(names)):
            raise ValueError(f"Maintenance optimization names must be unique in {self.tier}")
        for optimization in self.optimizations:
            if optimization.target.path[0] != self.tier:
                raise ValueError(
                    f"Maintenance target {optimization.target.path!r} crosses tier {self.tier!r}"
                )
        return self

    def policies(self) -> tuple[MaintenancePolicy, ...]:
        namespace: NamespacePath = (self.tier,)
        tier_target = PolicyTargetContract(type="namespace", path=namespace)
        policies = [
            MaintenancePolicy(
                name=f"mlh-{self.tier}-compact-metadata",
                namespace=namespace,
                owner=self.owner,
                policy_type="system.metadata-compaction",
                description=f"Compact Iceberg metadata in the {self.tier} tier.",
                content=MaintenancePolicyContent(),
                retention=self.metadata_retention,
                targets=(tier_target,),
            ),
            MaintenancePolicy(
                name=f"mlh-{self.tier}-expire-snapshots",
                namespace=namespace,
                owner=self.owner,
                policy_type="system.snapshot-expiry",
                description=f"Expire old Iceberg snapshots in the {self.tier} tier.",
                content=SnapshotExpiryPolicyContent(
                    config=SnapshotExpiryConfig(max_snapshot_age_days=self.snapshot_max_age_days)
                ),
                targets=(tier_target,),
            ),
            MaintenancePolicy(
                name=f"mlh-{self.tier}-remove-orphan-files",
                namespace=namespace,
                owner=self.owner,
                policy_type="system.orphan-file-removal",
                description=f"Remove aged orphan files from the {self.tier} tier.",
                content=OrphanFileRemovalPolicyContent(
                    max_orphan_file_age_in_days=self.orphan_file_max_age_days
                ),
                targets=(tier_target,),
            ),
        ]
        policies.extend(
            MaintenancePolicy(
                name=f"mlh-{self.tier}-{optimization.name}-compact-data-files",
                namespace=namespace,
                owner=self.owner,
                policy_type="system.data-compaction",
                description=(
                    f"Compact recent {optimization.name} partitions in the {self.tier} tier."
                ),
                content=DataCompactionPolicyContent(
                    config=DataCompactionConfig(
                        target_file_size_bytes=optimization.target_file_size_bytes
                    )
                ),
                execution=optimization,
                targets=(optimization.target,),
            )
            for optimization in self.optimizations
        )
        return tuple(policies)


class MaintenanceContract(ContractModel):
    version: Literal[1]
    tiers: tuple[TierMaintenanceContract, ...] = Field(
        min_length=len(LIFECYCLE_TIERS),
        max_length=len(LIFECYCLE_TIERS),
    )

    @model_validator(mode="after")
    def validate_lifecycle_tiers(self) -> Self:
        tiers = [tier.tier for tier in self.tiers]
        if len(tiers) != len(set(tiers)):
            raise ValueError("Maintenance tiers must be unique")
        if set(tiers) != set(LIFECYCLE_TIERS):
            raise ValueError("Maintenance must define landing, curated, and analytics")
        return self

    def policies(self) -> tuple[MaintenancePolicy, ...]:
        return tuple(
            policy
            for tier in sorted(self.tiers, key=lambda contract: contract.tier)
            for policy in tier.policies()
        )

    def metadata_retention(self, table: TableIdentifier) -> MetadataRetentionContract:
        policies = [
            policy
            for policy in self.policies()
            if policy.policy_type == "system.metadata-compaction"
            and any(
                (target.type == "namespace" and table.namespace[: len(target.path)] == target.path)
                or (target.type == "table-like" and table.iceberg == target.path)
                for target in policy.targets
            )
        ]
        if len(policies) != 1:
            raise ValueError(
                f"Table {table.iceberg!r} must have exactly one metadata retention policy; "
                f"found {len(policies)}"
            )
        retention = policies[0].retention
        if retention is None:
            raise ValueError(f"Table {table.iceberg!r} metadata policy has no retention contract")
        return retention
