from typing import Literal

from pydantic import Field, model_validator

from lakehouse_platform.contracts.base import (
    ContactContract,
    ContractModel,
    ContractName,
    Identifier,
    validate_relative_prefix,
)
from lakehouse_platform.contracts.tables import ManagedIcebergTableContract, TableIdentifier


class SourceContract(ContractModel):
    version: Literal[1]
    name: ContractName
    database: Identifier
    source_type: Literal["api", "rdbms", "stream"]
    owner: ContractName
    contact: ContactContract
    description: str = Field(min_length=1)
    raw_subpath: str | None = None
    tables: tuple[ManagedIcebergTableContract, ...] = Field(min_length=1)

    @property
    def storage_prefix(self) -> str:
        """Canonical physical boundary for every object owned by this source."""
        return f"{self.source_type}/{self.name}"

    @property
    def raw_object_prefix(self) -> str:
        """Canonical raw-object boundary with an optional source-owned subpath."""
        root = f"{self.storage_prefix}/raw"
        return f"{root}/{self.raw_subpath}" if self.raw_subpath else root

    @model_validator(mode="after")
    def validate_source_boundary(self) -> "SourceContract":
        if self.raw_subpath is not None:
            validate_relative_prefix(self.raw_subpath)
        keys = [table.key for table in self.tables]
        names = [table.name for table in self.tables]
        if len(keys) != len(set(keys)) or len(names) != len(set(names)):
            raise ValueError(f"Source {self.name!r} table keys and names must be unique")
        return self

    def table(self, key: str) -> ManagedIcebergTableContract:
        try:
            return next(table for table in self.tables if table.key == key)
        except StopIteration as error:
            raise KeyError(f"Unknown table {key!r} for source {self.name!r}") from error

    def table_identifier(self, key: str) -> TableIdentifier:
        return TableIdentifier(namespace=(self.database,), name=self.table(key).name)
