"""Compile declarative table contracts to PyIceberg SDK objects."""

from collections.abc import Sequence
from typing import Any

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

from lakehouse.contracts.base import ColumnContract
from lakehouse.contracts.tables import IcebergPartitionContract

ICEBERG_TYPES: dict[str, IcebergType] = {
    "string": StringType(),
    "long": LongType(),
    "boolean": BooleanType(),
    "timestamptz": TimestamptzType(),
    "date": DateType(),
}

TRANSFORMS: dict[str, Transform[Any, Any]] = {
    "identity": IdentityTransform(),
    "day": DayTransform(),
    "hour": HourTransform(),
    "month": MonthTransform(),
    "year": YearTransform(),
}


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
                field_type=ICEBERG_TYPES[column.data_type],
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
                transform=TRANSFORMS[partition.transform],
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
