from typing import Literal

from pydantic import Field, model_validator

from mini_lakehouse.contracts.base import (
    ContactContract,
    ContractModel,
    ContractName,
    Identifier,
    NamespacePath,
    PartitionTransformContract,
)
from mini_lakehouse.contracts.identifiers import TableIdentifier


class DomainTableContract(ContractModel):
    key: ContractName
    name: Identifier
    access: Literal["private", "protected", "public"]
    description: str = Field(min_length=1)
    grain: tuple[Identifier, ...] = Field(min_length=1)
    partitioning: tuple[PartitionTransformContract, ...] = ()


class DomainContract(ContractModel):
    version: Literal[1]
    name: ContractName
    owner: ContractName
    contact: ContactContract
    business_owner: ContractName
    description: str = Field(min_length=1)
    analytics_namespace: NamespacePath = Field(min_length=2)
    upstream_curated_products: tuple[ContractName, ...] = Field(min_length=1)
    tables: tuple[DomainTableContract, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_domain(self) -> "DomainContract":
        if self.analytics_namespace[0] != "analytics":
            raise ValueError(f"Domain {self.name!r} must publish below analytics")
        if len(self.upstream_curated_products) != len(set(self.upstream_curated_products)):
            raise ValueError(f"Domain {self.name!r} upstream curated products must be unique")
        keys = [table.key for table in self.tables]
        names = [table.name for table in self.tables]
        if len(keys) != len(set(keys)) or len(names) != len(set(names)):
            raise ValueError(f"Domain {self.name!r} table keys and names must be unique")
        for table in self.tables:
            if len(table.grain) != len(set(table.grain)):
                raise ValueError(f"Domain table {table.name!r} grain fields must be unique")
            partition_fields = [partition.field for partition in table.partitioning]
            if len(partition_fields) != len(set(partition_fields)):
                raise ValueError(f"Domain table {table.name!r} partition fields must be unique")
        return self

    def table(self, key: str) -> DomainTableContract:
        try:
            return next(table for table in self.tables if table.key == key)
        except StopIteration as error:
            raise KeyError(f"Unknown table {key!r} for domain {self.name!r}") from error

    def table_identifier(self, key: str) -> TableIdentifier:
        return TableIdentifier(namespace=self.analytics_namespace, name=self.table(key).name)
