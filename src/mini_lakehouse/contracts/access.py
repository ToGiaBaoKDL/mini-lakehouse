from typing import Annotated, Literal

from apache_polaris.sdk.management.models import CatalogPrivilege, NamespacePrivilege
from pydantic import Field, model_validator

from mini_lakehouse.contracts.base import (
    ContractModel,
    Identifier,
    NamespacePath,
)


class CatalogGrantContract(ContractModel):
    type: Literal["catalog"]
    privileges: tuple[CatalogPrivilege, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_privileges(self) -> "CatalogGrantContract":
        if len(self.privileges) != len(set(self.privileges)):
            raise ValueError("Catalog grant privileges must be unique")
        return self


class NamespaceGrantContract(ContractModel):
    type: Literal["namespace"]
    namespace: NamespacePath = Field(min_length=1)
    privileges: tuple[NamespacePrivilege, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_privileges(self) -> "NamespaceGrantContract":
        if len(self.privileges) != len(set(self.privileges)):
            raise ValueError(
                f"Namespace {'.'.join(self.namespace)!r} grant privileges must be unique"
            )
        return self


type ResourceGrantContract = Annotated[
    CatalogGrantContract | NamespaceGrantContract,
    Field(discriminator="type"),
]


class CatalogRoleContract(ContractModel):
    name: Identifier
    grants: tuple[ResourceGrantContract, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_grants(self) -> "CatalogRoleContract":
        keys = [
            (
                grant.type,
                grant.namespace if isinstance(grant, NamespaceGrantContract) else (),
                privilege.value,
            )
            for grant in self.grants
            for privilege in grant.privileges
        ]
        if len(keys) != len(set(keys)):
            raise ValueError(f"Catalog role {self.name!r} contains duplicate grants")
        return self


class ServiceIdentityContract(ContractModel):
    name: Identifier
    catalog_roles: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_catalog_roles(self) -> "ServiceIdentityContract":
        if len(self.catalog_roles) != len(set(self.catalog_roles)):
            raise ValueError(f"Service identity {self.name!r} references duplicate catalog roles")
        return self


class AccessContract(ContractModel):
    version: Literal[1]
    service_identities: tuple[ServiceIdentityContract, ...] = ()
    catalog_roles: tuple[CatalogRoleContract, ...] = ()

    @model_validator(mode="after")
    def validate_access_graph(self) -> "AccessContract":
        identity_names = [identity.name for identity in self.service_identities]
        if len(identity_names) != len(set(identity_names)):
            raise ValueError("Service identity names must be unique")
        role_names = [role.name for role in self.catalog_roles]
        if len(role_names) != len(set(role_names)):
            raise ValueError("Catalog role names must be unique")
        known_roles = set(role_names)
        for identity in self.service_identities:
            unknown = set(identity.catalog_roles) - known_roles
            if unknown:
                raise ValueError(
                    f"Service identity {identity.name!r} references unknown catalog roles "
                    f"{sorted(unknown)!r}"
                )
        return self
