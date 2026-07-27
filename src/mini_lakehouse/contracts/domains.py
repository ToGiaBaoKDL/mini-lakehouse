from typing import Literal

from pydantic import Field, model_validator

from mini_lakehouse.contracts.base import (
    ContactContract,
    ContractModel,
    ContractName,
    NamespacePath,
)


class DomainContract(ContractModel):
    version: Literal[1]
    name: ContractName
    owner: ContractName
    contact: ContactContract
    business_owner: ContractName
    description: str = Field(min_length=1)
    analytics_namespace: NamespacePath = Field(min_length=2)
    upstream_curated_products: tuple[ContractName, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_domain(self) -> "DomainContract":
        if self.analytics_namespace[0] != "analytics":
            raise ValueError(f"Domain {self.name!r} must publish below analytics")
        if len(self.upstream_curated_products) != len(set(self.upstream_curated_products)):
            raise ValueError(f"Domain {self.name!r} upstream curated products must be unique")
        return self
