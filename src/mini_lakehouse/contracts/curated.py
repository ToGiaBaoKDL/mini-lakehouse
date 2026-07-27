from typing import Literal

from pydantic import Field, model_validator

from mini_lakehouse.contracts.base import (
    ContactContract,
    ContractModel,
    ContractName,
    NamespacePath,
)
from mini_lakehouse.contracts.tables import ManagedIcebergTableContract, TableIdentifier


class CuratedProductContract(ContractModel):
    version: Literal[1]
    name: ContractName
    owner: ContractName
    contact: ContactContract
    description: str = Field(min_length=1)
    curated_namespace: NamespacePath = Field(min_length=2)
    upstream_sources: tuple[ContractName, ...] = Field(min_length=1)
    tables: tuple[ManagedIcebergTableContract, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_product(self) -> "CuratedProductContract":
        if self.curated_namespace[0] != "curated":
            raise ValueError(f"Curated product {self.name!r} must publish below curated")
        if len(self.upstream_sources) != len(set(self.upstream_sources)):
            raise ValueError(f"Curated product {self.name!r} upstream sources must be unique")
        keys = [table.key for table in self.tables]
        names = [table.name for table in self.tables]
        if len(keys) != len(set(keys)) or len(names) != len(set(names)):
            raise ValueError(f"Curated product {self.name!r} table keys and names must be unique")
        if missing_keys := [table.name for table in self.tables if not table.primary_key]:
            raise ValueError(f"Curated tables must declare a primary key: {missing_keys!r}")
        return self

    def table(self, key: str) -> ManagedIcebergTableContract:
        try:
            return next(table for table in self.tables if table.key == key)
        except StopIteration as error:
            raise KeyError(f"Unknown table {key!r} for curated product {self.name!r}") from error

    def table_identifier(self, key: str) -> TableIdentifier:
        return TableIdentifier(namespace=self.curated_namespace, name=self.table(key).name)
