from typing import Annotated, Literal

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


class HourlyPartitionCheckpoint(ContractModel):
    kind: Literal["hourly_partition"]
    field: Identifier


class TimestampCheckpoint(ContractModel):
    kind: Literal["timestamp"]
    field: Identifier


class CursorCheckpoint(ContractModel):
    kind: Literal["cursor"]
    field: Identifier


class OffsetCheckpoint(ContractModel):
    kind: Literal["offset"]
    field: Identifier


type CheckpointContract = Annotated[
    HourlyPartitionCheckpoint | TimestampCheckpoint | CursorCheckpoint | OffsetCheckpoint,
    Field(discriminator="kind"),
]


class SourceTableContract(ContractModel):
    key: ContractName
    name: Identifier
    schema_contract: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    columns: tuple[ColumnContract, ...] = Field(min_length=1)
    partitioning: tuple[PartitionTransformContract, ...] = Field(min_length=1)
    write_mode: Literal["append", "checkpoint_overwrite"]

    @model_validator(mode="after")
    def validate_table(self) -> "SourceTableContract":
        column_names = [column.name for column in self.columns]
        field_ids = [column.field_id for column in self.columns]
        if len(column_names) != len(set(column_names)):
            raise ValueError(f"Table {self.name!r} column names must be unique")
        if len(field_ids) != len(set(field_ids)) or any(field_id < 1 for field_id in field_ids):
            raise ValueError(f"Table {self.name!r} field IDs must be unique positive integers")
        partition_fields = [partition.field for partition in self.partitioning]
        if len(partition_fields) != len(set(partition_fields)):
            raise ValueError(f"Table {self.name!r} partition fields must be unique")
        unknown_partition_fields = set(partition_fields) - set(column_names)
        if unknown_partition_fields:
            raise ValueError(
                f"Table {self.name!r} partitions by unknown columns "
                f"{sorted(unknown_partition_fields)!r}"
            )
        partition_names = [partition.name for partition in self.partitioning if partition.name]
        if len(partition_names) != len(set(partition_names)):
            raise ValueError(f"Table {self.name!r} partition names must be unique")
        return self


class SourceContract(ContractModel):
    version: Literal[1]
    name: ContractName
    source_type: Literal["api", "rdbms", "stream"]
    owner: ContractName
    contact: ContactContract
    description: str = Field(min_length=1)
    landing_namespace: NamespacePath = Field(min_length=1)
    raw_object_prefix: str
    checkpoint: CheckpointContract
    tables: tuple[SourceTableContract, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source_boundary(self) -> "SourceContract":
        validate_relative_prefix(self.raw_object_prefix)
        if self.landing_namespace != ("landing",):
            raise ValueError(f"Source {self.name!r} must publish in the shared landing namespace")
        source_object_prefix = f"{self.source_type}/{self.name}/"
        if not self.raw_object_prefix.startswith(source_object_prefix):
            raise ValueError(
                f"Source {self.name!r} raw objects must live below {source_object_prefix!r}"
            )
        keys = [table.key for table in self.tables]
        names = [table.name for table in self.tables]
        if len(keys) != len(set(keys)) or len(names) != len(set(names)):
            raise ValueError(f"Source {self.name!r} table keys and names must be unique")
        table_name_prefix = f"{self.name.replace('-', '_')}_"
        for table in self.tables:
            if not table.name.startswith(table_name_prefix):
                raise ValueError(
                    f"Source table {table.name!r} must start with {table_name_prefix!r}"
                )
            if table.write_mode == "checkpoint_overwrite" and self.checkpoint.field not in {
                partition.field for partition in table.partitioning
            }:
                raise ValueError(
                    f"Table {table.name!r} must partition by checkpoint field "
                    f"{self.checkpoint.field!r} for idempotent overwrite"
                )
        return self

    def table(self, key: str) -> SourceTableContract:
        try:
            return next(table for table in self.tables if table.key == key)
        except StopIteration as error:
            raise KeyError(f"Unknown table {key!r} for source {self.name!r}") from error

    def table_identifier(self, key: str) -> TableIdentifier:
        return TableIdentifier(namespace=self.landing_namespace, name=self.table(key).name)
