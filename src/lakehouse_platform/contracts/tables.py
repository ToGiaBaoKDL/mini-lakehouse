import re
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from lakehouse_platform.contracts.base import (
    ColumnContract,
    ContractModel,
    ContractName,
    Identifier,
)

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TEMPORAL_PARTITION_TYPES = frozenset({"date", "timestamptz"})


def _validate_identifier(value: str) -> None:
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid catalog identifier: {value!r}")


def _quoted(value: str) -> str:
    _validate_identifier(value)
    return f'"{value}"'


@dataclass(frozen=True, slots=True)
class TableIdentifier:
    namespace: tuple[str, ...]
    name: str

    def __post_init__(self) -> None:
        if not self.namespace:
            raise ValueError("A table must belong to a namespace")
        for part in (*self.namespace, self.name):
            _validate_identifier(part)

    @property
    def iceberg(self) -> tuple[str, ...]:
        return (*self.namespace, self.name)

    def athena(self) -> str:
        if len(self.namespace) != 1:
            raise ValueError("AWS Glue tables require exactly one database")
        return f"{_quoted(self.namespace[0])}.{_quoted(self.name)}"


class IcebergPartitionContract(ContractModel):
    field_id: int = Field(ge=1000)
    field: Identifier
    transform: Literal["identity", "day", "hour", "month", "year"]
    name: Identifier | None = None


class ManagedIcebergTableContract(ContractModel):
    key: ContractName
    name: Identifier
    description: str = Field(min_length=1)
    columns: tuple[ColumnContract, ...] = Field(min_length=1)
    primary_key: tuple[Identifier, ...] = ()
    partitioning: tuple[IcebergPartitionContract, ...] = ()

    @model_validator(mode="after")
    def validate_table(self) -> "ManagedIcebergTableContract":
        column_names = [column.name for column in self.columns]
        column_field_ids = [column.field_id for column in self.columns]
        if len(column_names) != len(set(column_names)):
            raise ValueError(f"Table {self.name!r} column names must be unique")
        if len(column_field_ids) != len(set(column_field_ids)) or any(
            field_id < 1 for field_id in column_field_ids
        ):
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
        partition_field_ids = [partition.field_id for partition in self.partitioning]
        partition_names = [
            partition.name
            or (
                partition.field
                if partition.transform == "identity"
                else f"{partition.field}_{partition.transform}"
            )
            for partition in self.partitioning
        ]
        if len(partition_fields) != len(set(partition_fields)):
            raise ValueError(f"Table {self.name!r} partition fields must be unique")
        if len(partition_field_ids) != len(set(partition_field_ids)):
            raise ValueError(f"Table {self.name!r} partition field IDs must be unique")
        if len(partition_names) != len(set(partition_names)):
            raise ValueError(f"Table {self.name!r} partition names must be unique")
        if unknown_partition_fields := set(partition_fields) - set(column_names):
            raise ValueError(
                f"Table {self.name!r} partitions by unknown columns "
                f"{sorted(unknown_partition_fields)!r}"
            )
        column_types = {column.name: column.data_type for column in self.columns}
        for partition in self.partitioning:
            data_type = column_types[partition.field]
            if partition.transform in {"year", "month", "day"} and (
                data_type not in _TEMPORAL_PARTITION_TYPES
            ):
                raise ValueError(
                    f"Table {self.name!r} cannot apply {partition.transform!r} "
                    f"to {data_type!r} column {partition.field!r}"
                )
            if partition.transform == "hour" and data_type != "timestamptz":
                raise ValueError(
                    f"Table {self.name!r} cannot apply 'hour' to "
                    f"{data_type!r} column {partition.field!r}"
                )
        return self
