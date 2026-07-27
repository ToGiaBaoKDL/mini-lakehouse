from typing import Literal

from pydantic import Field, field_validator, model_validator

from mini_lakehouse.contracts.base import (
    LIFECYCLE_TIERS,
    ContractModel,
    ContractName,
    Identifier,
    NamespacePath,
    StorageTier,
)


class CatalogSpec(ContractModel):
    name: Identifier
    type: Literal["INTERNAL"] = "INTERNAL"
    owner: ContractName
    default_storage_root: StorageTier
    namespace_custom_locations: bool = True
    properties: dict[str, str] = Field(default_factory=dict)

    @field_validator("properties")
    @classmethod
    def reject_managed_properties(cls, value: dict[str, str]) -> dict[str, str]:
        managed = {
            "owner",
            "default-base-location",
            "polaris.config.namespace-custom-location.enabled",
        }
        if overlap := managed.intersection(value):
            raise ValueError(f"Catalog properties are managed by typed fields: {sorted(overlap)}")
        return value


class NamespaceContract(ContractModel):
    path: NamespacePath = Field(min_length=1)
    storage_root: StorageTier | None = None
    owner: ContractName
    description: str = Field(min_length=1)
    properties: dict[str, str] = Field(default_factory=dict)

    def iceberg_properties(self, storage_uri: str) -> dict[str, str]:
        return {"owner": self.owner, **self.properties, "location": storage_uri}


class PlatformContract(ContractModel):
    version: Literal[1]
    catalog: CatalogSpec
    namespaces: tuple[NamespaceContract, ...]

    @model_validator(mode="after")
    def validate_namespace_tree(self) -> "PlatformContract":
        paths = [namespace.path for namespace in self.namespaces]
        if len(paths) != len(set(paths)):
            raise ValueError("Namespace paths must be unique")
        for namespace in self.namespaces:
            if namespace.storage_root is None or namespace.path != (namespace.storage_root,):
                raise ValueError(
                    "Platform namespaces must be lifecycle roots; products and domains own "
                    "their child namespaces"
                )
        if {namespace.storage_root for namespace in self.namespaces} != set(LIFECYCLE_TIERS):
            raise ValueError("Platform contract must define all lifecycle storage roots")
        return self
