from typing import Literal

from pydantic import Field, model_validator

from mini_lakehouse.contracts.base import ContractModel, Identifier

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


class AccessContract(ContractModel):
    version: Literal[1]
    catalog_role_grants: tuple[CatalogRoleGrantContract, ...] = ()

    @model_validator(mode="after")
    def validate_unique_roles(self) -> "AccessContract":
        names = [grant.catalog_role for grant in self.catalog_role_grants]
        if len(names) != len(set(names)):
            raise ValueError("Catalog role grant entries must be unique")
        return self
