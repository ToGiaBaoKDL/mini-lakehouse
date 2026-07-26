from typing import Literal

from pydantic import Field, field_validator, model_validator

from mini_lakehouse.contracts.base import (
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


type CatalogPrivilege = Literal[
    "CATALOG_MANAGE_ACCESS",
    "CATALOG_MANAGE_CONTENT",
    "CATALOG_MANAGE_METADATA",
    "CATALOG_READ_PROPERTIES",
    "CATALOG_WRITE_PROPERTIES",
    "CATALOG_ATTACH_POLICY",
    "CATALOG_DETACH_POLICY",
]


class CatalogRoleGrantContract(ContractModel):
    catalog_role: Identifier
    privileges: tuple[CatalogPrivilege, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_privileges(self) -> "CatalogRoleGrantContract":
        if len(self.privileges) != len(set(self.privileges)):
            raise ValueError(f"Catalog role {self.catalog_role!r} contains duplicate privileges")
        return self


class NamespaceContract(ContractModel):
    path: NamespacePath = Field(min_length=1)
    storage_root: StorageTier | None = None
    owner: ContractName
    description: str = Field(min_length=1)
    properties: dict[str, str] = Field(default_factory=dict)

    def iceberg_properties(self, storage_uri: str | None = None) -> dict[str, str]:
        properties = {"owner": self.owner, **self.properties}
        if storage_uri is not None:
            properties["location"] = storage_uri
        return properties


class CatalogContract(ContractModel):
    version: Literal[1]
    catalog: CatalogSpec
    namespaces: tuple[NamespaceContract, ...]
    catalog_role_grants: tuple[CatalogRoleGrantContract, ...] = ()

    @model_validator(mode="after")
    def validate_namespace_tree(self) -> "CatalogContract":
        paths = [namespace.path for namespace in self.namespaces]
        if len(paths) != len(set(paths)):
            raise ValueError("Namespace paths must be unique")
        role_names = [grant.catalog_role for grant in self.catalog_role_grants]
        if len(role_names) != len(set(role_names)):
            raise ValueError("Catalog role grant entries must be unique")
        for namespace in self.namespaces:
            root = namespace.path[0]
            if root not in {"landing", "curated", "analytics"}:
                raise ValueError(
                    f"Namespace {'.'.join(namespace.path)!r} is outside a lifecycle root"
                )
            if namespace.storage_root is None or namespace.path != (namespace.storage_root,):
                raise ValueError(
                    "Catalog namespaces must be lifecycle roots; products and domains own "
                    "their child namespaces"
                )
        roots = {
            namespace.storage_root
            for namespace in self.namespaces
            if namespace.storage_root is not None
        }
        if roots != {"landing", "curated", "analytics"}:
            raise ValueError("Catalog contract must define all three lifecycle storage roots")
        return self

    def namespace(self, path: NamespacePath) -> NamespaceContract:
        try:
            return next(namespace for namespace in self.namespaces if namespace.path == path)
        except StopIteration as error:
            raise KeyError(f"Unknown namespace: {'.'.join(path)}") from error
