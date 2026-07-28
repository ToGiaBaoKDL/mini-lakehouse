import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import pyarrow as pa
from pydantic import Field, model_validator
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import (
    DayTransform,
    HourTransform,
    IdentityTransform,
    MonthTransform,
    Transform,
    YearTransform,
)
from pyiceberg.types import (
    BooleanType,
    DateType,
    IcebergType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

from mini_lakehouse.contracts.base import (
    ColumnContract,
    ContractModel,
    ContractName,
    Identifier,
)

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_ARROW_TYPES: dict[str, pa.DataType] = {
    "string": pa.string(),
    "long": pa.int64(),
    "boolean": pa.bool_(),
    "timestamptz": pa.timestamp("us", tz="UTC"),
    "date": pa.date32(),
}

_ICEBERG_TYPES: dict[str, IcebergType] = {
    "string": StringType(),
    "long": LongType(),
    "boolean": BooleanType(),
    "timestamptz": TimestamptzType(),
    "date": DateType(),
}

_TRANSFORMS: dict[str, Transform[Any, Any]] = {
    "identity": IdentityTransform(),
    "day": DayTransform(),
    "hour": HourTransform(),
    "month": MonthTransform(),
    "year": YearTransform(),
}


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

    @classmethod
    def from_iceberg(cls, identifier: tuple[str, ...]) -> "TableIdentifier":
        if len(identifier) < 2:
            raise ValueError(f"Expected namespace and table name, got {identifier!r}")
        return cls(namespace=identifier[:-1], name=identifier[-1])

    @property
    def iceberg(self) -> tuple[str, ...]:
        return (*self.namespace, self.name)

    def trino(self, catalog: str) -> str:
        schema = ".".join(self.namespace)
        return ".".join((_quoted(catalog), f'"{schema}"', _quoted(self.name)))


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
        return self


def arrow_schema(columns: Sequence[ColumnContract]) -> pa.Schema:
    return pa.schema(
        [
            pa.field(column.name, _ARROW_TYPES[column.data_type], nullable=not column.required)
            for column in columns
        ]
    )


def iceberg_schema(
    columns: Sequence[ColumnContract],
    primary_key: Sequence[str],
) -> Schema:
    field_ids = {column.name: column.field_id for column in columns}
    return Schema(
        *[
            NestedField(
                field_id=column.field_id,
                name=column.name,
                field_type=_ICEBERG_TYPES[column.data_type],
                required=column.required,
            )
            for column in columns
        ],
        identifier_field_ids=[field_ids[name] for name in primary_key],
    )


def partition_spec(
    columns: Sequence[ColumnContract],
    partitioning: Sequence[IcebergPartitionContract],
) -> PartitionSpec:
    field_ids = {column.name: column.field_id for column in columns}
    return PartitionSpec(
        *[
            PartitionField(
                source_id=field_ids[partition.field],
                field_id=partition.field_id,
                transform=_TRANSFORMS[partition.transform],
                name=partition.name
                or (
                    partition.field
                    if partition.transform == "identity"
                    else f"{partition.field}_{partition.transform}"
                ),
            )
            for partition in partitioning
        ]
    )
