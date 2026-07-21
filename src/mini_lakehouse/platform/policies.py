import json
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from mini_lakehouse.contracts import TableIdentifier
from mini_lakehouse.contracts.policies import (
    DataCompactionContent,
    MetadataCompactionContent,
    OrphanFileRemovalContent,
    SnapshotExpiryContent,
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


def _data_size(value: int) -> str:
    mebibyte = 1024 * 1024
    if value % mebibyte == 0:
        return f"{value // mebibyte}MB"
    return f"{value}B"


def maintenance_statements(
    table: TableIdentifier,
    catalog: str,
    policies: list[PolarisPolicy],
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
    for policy_type in MAINTENANCE_POLICY_TYPES:
        policy = applicable.get(policy_type)
        if policy is None:
            continue
        content = policy.content_object()
        if policy_type == "system.data-compaction":
            parsed = DataCompactionContent.model_validate(content)
            if parsed.enable:
                threshold = _data_size(parsed.config.target_file_size_bytes)
                statements.append(
                    f"ALTER TABLE {relation} EXECUTE optimize(file_size_threshold => '{threshold}')"
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
