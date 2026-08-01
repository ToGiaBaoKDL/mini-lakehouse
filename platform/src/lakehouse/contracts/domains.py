from typing import Literal

from pydantic import Field, model_validator

from lakehouse.contracts.base import (
    ContactContract,
    ContractModel,
    ContractName,
    Identifier,
)


class DomainContract(ContractModel):
    version: Literal[1]
    name: ContractName
    database: Identifier
    owner: ContractName
    contact: ContactContract
    business_owner: ContractName
    description: str = Field(min_length=1)
    upstream_curated_products: tuple[ContractName, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_domain(self) -> "DomainContract":
        if len(self.upstream_curated_products) != len(set(self.upstream_curated_products)):
            raise ValueError(f"Domain {self.name!r} upstream curated products must be unique")
        return self
