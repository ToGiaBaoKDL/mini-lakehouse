import json
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from mini_lakehouse.contracts import TableIdentifier

POLICY_SCHEMA_VERSION = "2025-02-03"
POLICY_NAMESPACE = ("curated",)
MAINTENANCE_POLICY_TYPES = (
    "system.data-compaction",
    "system.metadata-compaction",
    "system.snapshot-expiry",
    "system.orphan-file-removal",
)


class DataCompactionConfig(BaseModel):
    target_file_size_bytes: int = Field(ge=1024 * 1024)


class DataCompactionContent(BaseModel):
    version: Literal["2025-02-03"] = POLICY_SCHEMA_VERSION
    enable: bool = True
    config: DataCompactionConfig


class MetadataCompactionContent(BaseModel):
    version: Literal["2025-02-03"] = POLICY_SCHEMA_VERSION
    enable: bool = True


class SnapshotExpiryConfig(BaseModel):
    max_snapshot_age_days: int = Field(ge=7)


class SnapshotExpiryContent(BaseModel):
    version: Literal["2025-02-03"] = POLICY_SCHEMA_VERSION
    enable: bool = True
    config: SnapshotExpiryConfig


class OrphanFileRemovalContent(BaseModel):
    version: Literal["2025-02-03"] = POLICY_SCHEMA_VERSION
    enable: bool = True
    max_orphan_file_age_in_days: int = Field(ge=7)


class PolicySpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    namespace: tuple[str, ...] = POLICY_NAMESPACE
    name: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    policy_type: str
    description: str
    content: dict[str, Any]
    target_namespaces: tuple[tuple[str, ...], ...]

    @property
    def content_json(self) -> str:
        return json.dumps(self.content, sort_keys=True, separators=(",", ":"))


class PolarisPolicy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    policy_type: str = Field(validation_alias=AliasChoices("policy-type", "type"))
    description: str = ""
    content: str | dict[str, Any]
    version: int = Field(ge=0)
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
    applicable_policies: list[PolarisPolicy] = Field(
        default_factory=list,
        validation_alias=AliasChoices("applicable-policies", "policies"),
    )


def maintenance_policy_contract() -> tuple[PolicySpec, ...]:
    targets = (("landing",), ("curated",), ("analytics",))
    return (
        PolicySpec(
            name="compact-data-files",
            policy_type="system.data-compaction",
            description="Compact small Iceberg data files to a 128 MiB target.",
            content=DataCompactionContent(
                config=DataCompactionConfig(target_file_size_bytes=128 * 1024 * 1024)
            ).model_dump(mode="json"),
            target_namespaces=targets,
        ),
        PolicySpec(
            name="compact-metadata",
            policy_type="system.metadata-compaction",
            description="Rewrite Iceberg manifests for efficient planning.",
            content=MetadataCompactionContent().model_dump(mode="json"),
            target_namespaces=targets,
        ),
        PolicySpec(
            name="expire-snapshots",
            policy_type="system.snapshot-expiry",
            description="Expire Iceberg snapshots older than seven days.",
            content=SnapshotExpiryContent(
                config=SnapshotExpiryConfig(max_snapshot_age_days=7)
            ).model_dump(mode="json"),
            target_namespaces=targets,
        ),
        PolicySpec(
            name="remove-orphan-files",
            policy_type="system.orphan-file-removal",
            description="Remove Iceberg orphan files only after a thirty-day safety window.",
            content=OrphanFileRemovalContent(max_orphan_file_age_in_days=30).model_dump(
                mode="json"
            ),
            target_namespaces=targets,
        ),
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
