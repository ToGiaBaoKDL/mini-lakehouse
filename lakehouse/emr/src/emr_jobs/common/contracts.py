"""Load the validated contract artifact and adapt its schemas to Spark."""

from lakehouse.contracts.base import ColumnContract
from pyspark.sql.types import (
    BooleanType,
    DataType,
    DateType,
    DecimalType,
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from emr_jobs.common.s3 import read_bytes
from lakehouse.contracts import (
    DataContracts,
    ManagedIcebergTableContract,
)

SPARK_TYPES = {
    "boolean": BooleanType(),
    "date": DateType(),
    "double": DoubleType(),
    "long": LongType(),
    "string": StringType(),
    "timestamptz": TimestampType(),
}


def spark_type(column: ColumnContract) -> DataType:
    if column.data_type == "decimal":
        if column.precision is None or column.scale is None:
            raise ValueError(f"Decimal column {column.name!r} has no precision or scale")
        return DecimalType(column.precision, column.scale)
    return SPARK_TYPES[column.data_type]


def load_contracts(uri: str) -> DataContracts:
    return DataContracts.model_validate_json(read_bytes(uri))


def spark_schema(table: ManagedIcebergTableContract) -> StructType:
    return StructType(
        [
            StructField(
                column.name,
                spark_type(column),
                nullable=not column.required,
            )
            for column in table.columns
        ]
    )
