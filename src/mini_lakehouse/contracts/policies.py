import json
from typing import Annotated, Literal

from pydantic import Field

from mini_lakehouse.contracts.base import (
    ContractModel,
    ContractName,
    Identifier,
    NamespacePath,
)


class DataCompactionConfig(ContractModel):
    target_file_size_bytes: int = Field(ge=1024 * 1024)


class DataCompactionContent(ContractModel):
    version: Literal["2025-02-03"]
    enable: bool
    config: DataCompactionConfig


class BoundedOptimizeExecution(ContractModel):
    partition_field: Identifier
    partition_type: Literal["date", "timestamptz"]
    lookback_days: int = Field(ge=1, le=365)


class MetadataCompactionContent(ContractModel):
    version: Literal["2025-02-03"]
    enable: bool


class SnapshotExpiryConfig(ContractModel):
    max_snapshot_age_days: int = Field(ge=7)


class SnapshotExpiryContent(ContractModel):
    version: Literal["2025-02-03"]
    enable: bool
    config: SnapshotExpiryConfig


class OrphanFileRemovalContent(ContractModel):
    version: Literal["2025-02-03"]
    enable: bool
    max_orphan_file_age_in_days: int = Field(ge=7)


class CatalogPolicyTarget(ContractModel):
    type: Literal["catalog"]
    path: tuple[()] = ()


class NamespacePolicyTarget(ContractModel):
    type: Literal["namespace"]
    path: NamespacePath = Field(min_length=1)


class TablePolicyTarget(ContractModel):
    type: Literal["table-like"]
    path: tuple[Identifier, ...] = Field(min_length=2)


type PolicyTarget = Annotated[
    CatalogPolicyTarget | NamespacePolicyTarget | TablePolicyTarget,
    Field(discriminator="type"),
]


class PolicyContractBase(ContractModel):
    version: Literal[1]
    name: ContractName
    namespace: NamespacePath = Field(min_length=1)
    owner: ContractName
    description: str = Field(min_length=1)
    targets: tuple[PolicyTarget, ...] = Field(min_length=1)


class DataCompactionPolicyContract(PolicyContractBase):
    policy_type: Literal["system.data-compaction"]
    content: DataCompactionContent
    execution: BoundedOptimizeExecution


class MetadataCompactionPolicyContract(PolicyContractBase):
    policy_type: Literal["system.metadata-compaction"]
    content: MetadataCompactionContent


class SnapshotExpiryPolicyContract(PolicyContractBase):
    policy_type: Literal["system.snapshot-expiry"]
    content: SnapshotExpiryContent


class OrphanFileRemovalPolicyContract(PolicyContractBase):
    policy_type: Literal["system.orphan-file-removal"]
    content: OrphanFileRemovalContent


type PolicyContract = Annotated[
    DataCompactionPolicyContract
    | MetadataCompactionPolicyContract
    | SnapshotExpiryPolicyContract
    | OrphanFileRemovalPolicyContract,
    Field(discriminator="policy_type"),
]


def policy_content_object(policy: PolicyContract) -> dict[str, object]:
    content = policy.content.model_dump(mode="json")
    return {str(key): value for key, value in content.items()}


def policy_content_json(policy: PolicyContract) -> str:
    return json.dumps(policy_content_object(policy), sort_keys=True, separators=(",", ":"))
