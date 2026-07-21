from typing import Annotated, Literal

from pydantic import Field, model_validator

from mini_lakehouse.contracts.base import (
    ContractModel,
    ContractName,
    Identifier,
    NamespacePath,
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
    location_prefix: str
    schema_contract: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    partition_fields: tuple[Identifier, ...] = Field(min_length=1)
    write_mode: Literal["append", "partition_overwrite"]

    @model_validator(mode="after")
    def validate_location_prefix(self) -> "SourceTableContract":
        validate_relative_prefix(self.location_prefix)
        return self


class SourceContract(ContractModel):
    version: Literal[1]
    name: ContractName
    source_type: Literal["api", "rdbms", "stream"]
    owner: ContractName
    dbt_group: Identifier
    description: str = Field(min_length=1)
    landing_namespace: NamespacePath = Field(min_length=3)
    raw_object_prefix: str
    checkpoint: CheckpointContract
    tables: tuple[SourceTableContract, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source_boundary(self) -> "SourceContract":
        validate_relative_prefix(self.raw_object_prefix)
        expected_prefix = ("landing", self.source_type)
        if self.landing_namespace[:2] != expected_prefix:
            raise ValueError(f"Source {self.name!r} must live below {'.'.join(expected_prefix)!r}")
        source_object_prefix = f"{self.source_type}/{self.name}/"
        if not self.raw_object_prefix.startswith(source_object_prefix):
            raise ValueError(
                f"Source {self.name!r} raw objects must live below {source_object_prefix!r}"
            )
        keys = [table.key for table in self.tables]
        names = [table.name for table in self.tables]
        locations = [table.location_prefix for table in self.tables]
        if len(keys) != len(set(keys)) or len(names) != len(set(names)):
            raise ValueError(f"Source {self.name!r} table keys and names must be unique")
        if len(locations) != len(set(locations)):
            raise ValueError(f"Source {self.name!r} table locations must be unique")
        for table in self.tables:
            if not table.location_prefix.startswith(source_object_prefix):
                raise ValueError(
                    f"Source {self.name!r} tables must live below {source_object_prefix!r}"
                )
            if (
                table.write_mode == "partition_overwrite"
                and self.checkpoint.field not in table.partition_fields
            ):
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
