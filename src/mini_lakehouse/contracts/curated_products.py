from typing import Literal

from pydantic import Field, model_validator

from mini_lakehouse.contracts.base import (
    ColumnContract,
    ContactContract,
    ContractModel,
    ContractName,
    Identifier,
    NamespacePath,
    PartitionTransformContract,
    validate_relative_prefix,
)
from mini_lakehouse.contracts.identifiers import TableIdentifier


class CuratedTableContract(ContractModel):
    key: ContractName
    name: Identifier
    description: str = Field(min_length=1)
    location_prefix: str
    schema_contract: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    columns: tuple[ColumnContract, ...] = Field(min_length=1)
    primary_key: tuple[Identifier, ...] = Field(min_length=1)
    partitioning: tuple[PartitionTransformContract, ...] = ()
    write_mode: Literal["merge"] = "merge"

    @model_validator(mode="after")
    def validate_table(self) -> "CuratedTableContract":
        validate_relative_prefix(self.location_prefix)
        column_names = [column.name for column in self.columns]
        field_ids = [column.field_id for column in self.columns]
        if len(column_names) != len(set(column_names)):
            raise ValueError(f"Table {self.name!r} column names must be unique")
        if len(field_ids) != len(set(field_ids)) or any(field_id < 1 for field_id in field_ids):
            raise ValueError(f"Table {self.name!r} field IDs must be unique positive integers")
        if len(self.primary_key) != len(set(self.primary_key)):
            raise ValueError(f"Table {self.name!r} primary key fields must be unique")
        if unknown_keys := set(self.primary_key) - set(column_names):
            raise ValueError(
                f"Table {self.name!r} primary key references unknown columns "
                f"{sorted(unknown_keys)!r}"
            )
        required_columns = {column.name for column in self.columns if column.required}
        if missing_required_keys := set(self.primary_key) - required_columns:
            raise ValueError(
                f"Table {self.name!r} primary key columns must be required: "
                f"{sorted(missing_required_keys)!r}"
            )
        partition_fields = [partition.field for partition in self.partitioning]
        if len(partition_fields) != len(set(partition_fields)):
            raise ValueError(f"Table {self.name!r} partition fields must be unique")
        if unknown_partition_fields := set(partition_fields) - set(column_names):
            raise ValueError(
                f"Table {self.name!r} partitions by unknown columns "
                f"{sorted(unknown_partition_fields)!r}"
            )
        return self


class CuratedProductContract(ContractModel):
    version: Literal[1]
    name: ContractName
    owner: ContractName
    contact: ContactContract
    description: str = Field(min_length=1)
    curated_namespace: NamespacePath = Field(min_length=2)
    upstream_sources: tuple[ContractName, ...] = Field(min_length=1)
    tables: tuple[CuratedTableContract, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_product(self) -> "CuratedProductContract":
        if self.curated_namespace[0] != "curated":
            raise ValueError(f"Curated product {self.name!r} must publish below curated")
        if len(self.upstream_sources) != len(set(self.upstream_sources)):
            raise ValueError(f"Curated product {self.name!r} upstream sources must be unique")
        keys = [table.key for table in self.tables]
        names = [table.name for table in self.tables]
        locations = [table.location_prefix for table in self.tables]
        if len(keys) != len(set(keys)) or len(names) != len(set(names)):
            raise ValueError(f"Curated product {self.name!r} table keys and names must be unique")
        if len(locations) != len(set(locations)):
            raise ValueError(f"Curated product {self.name!r} table locations must be unique")
        product_prefix = f"{self.name}/"
        if any(not location.startswith(product_prefix) for location in locations):
            raise ValueError(
                f"Curated product {self.name!r} table locations must live below {product_prefix!r}"
            )
        return self

    def table(self, key: str) -> CuratedTableContract:
        try:
            return next(table for table in self.tables if table.key == key)
        except StopIteration as error:
            raise KeyError(f"Unknown table {key!r} for curated product {self.name!r}") from error

    def table_identifier(self, key: str) -> TableIdentifier:
        return TableIdentifier(namespace=self.curated_namespace, name=self.table(key).name)
