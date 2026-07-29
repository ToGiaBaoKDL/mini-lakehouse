from typing import Literal

from pydantic import Field, model_validator

from lakehouse_platform.contracts.base import (
    ContactContract,
    ContractModel,
    ContractName,
    Identifier,
)
from lakehouse_platform.contracts.tables import (
    ManagedIcebergTableContract,
    TableIdentifier,
)


class CuratedProductContract(ContractModel):
    version: Literal[1]
    name: ContractName
    database: Identifier
    owner: ContractName
    contact: ContactContract
    description: str = Field(min_length=1)
    upstream_sources: tuple[ContractName, ...] = Field(min_length=1)
    tables: tuple[ManagedIcebergTableContract, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_product(self) -> "CuratedProductContract":
        if len(self.upstream_sources) != len(set(self.upstream_sources)):
            raise ValueError(f"Curated product {self.name!r} upstream sources must be unique")
        keys = [table.key for table in self.tables]
        names = [table.name for table in self.tables]
        if len(keys) != len(set(keys)) or len(names) != len(set(names)):
            raise ValueError(f"Curated product {self.name!r} table keys and names must be unique")
        return self

    def table(self, key: str) -> ManagedIcebergTableContract:
        try:
            return next(table for table in self.tables if table.key == key)
        except StopIteration as error:
            raise KeyError(f"Unknown table {key!r} for curated product {self.name!r}") from error

    def table_identifier(self, key: str) -> TableIdentifier:
        return TableIdentifier(namespace=(self.database,), name=self.table(key).name)
