"""Runtime schema adapters for the canonical logical types in YAML contracts."""

from collections.abc import Sequence
from typing import Any

import pyarrow as pa
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

from mini_lakehouse.contracts.base import ColumnContract, PartitionTransformContract

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

_TRINO_TYPES = {
    "string": "varchar",
    "long": "bigint",
    "boolean": "boolean",
    "timestamptz": "timestamp(6) with time zone",
    "date": "date",
}

_TRANSFORMS: dict[str, Transform[Any, Any]] = {
    "identity": IdentityTransform(),
    "day": DayTransform(),
    "hour": HourTransform(),
    "month": MonthTransform(),
    "year": YearTransform(),
}


def arrow_schema(columns: Sequence[ColumnContract]) -> pa.Schema:
    return pa.schema(
        [
            pa.field(column.name, _ARROW_TYPES[column.data_type], nullable=not column.required)
            for column in columns
        ]
    )


def iceberg_schema(columns: Sequence[ColumnContract]) -> Schema:
    return Schema(
        *[
            NestedField(
                field_id=column.field_id,
                name=column.name,
                field_type=_ICEBERG_TYPES[column.data_type],
                required=column.required,
            )
            for column in columns
        ]
    )


def trino_type(column: ColumnContract) -> str:
    return _TRINO_TYPES[column.data_type]


def partition_expression(partition: PartitionTransformContract) -> str:
    if partition.transform == "identity":
        return partition.field
    return f"{partition.transform}({partition.field})"


def partition_spec(
    columns: Sequence[ColumnContract],
    partitioning: Sequence[PartitionTransformContract],
) -> PartitionSpec:
    field_ids = {column.name: column.field_id for column in columns}
    return PartitionSpec(
        *[
            PartitionField(
                source_id=field_ids[partition.field],
                field_id=1000 + index,
                transform=_TRANSFORMS[partition.transform],
                name=partition.name
                or (
                    partition.field
                    if partition.transform == "identity"
                    else f"{partition.field}_{partition.transform}"
                ),
            )
            for index, partition in enumerate(partitioning)
        ]
    )
