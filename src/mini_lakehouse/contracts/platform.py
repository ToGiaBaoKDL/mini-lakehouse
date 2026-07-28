from typing import Literal

from pydantic import Field, model_validator

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


class NamespaceContract(ContractModel):
    path: NamespacePath = Field(min_length=1)
    owner: ContractName
    description: str = Field(min_length=1)
    properties: dict[str, str] = Field(default_factory=dict)


class PlatformContract(ContractModel):
    version: Literal[1]
    catalog: CatalogSpec
    namespaces: tuple[NamespaceContract, ...]

    @model_validator(mode="after")
    def validate_namespace_tree(self) -> "PlatformContract":
        paths = [namespace.path for namespace in self.namespaces]
        if len(paths) != len(set(paths)):
            raise ValueError("Namespace paths must be unique")
        expected_paths = {(tier,) for tier in LIFECYCLE_TIERS}
        if set(paths) != expected_paths:
            raise ValueError(
                "Platform contract must define exactly the landing, curated, and analytics roots"
            )
        return self
