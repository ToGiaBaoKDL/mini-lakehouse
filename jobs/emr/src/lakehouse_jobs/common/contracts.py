"""Load the validated contract artifact and adapt its schemas to Spark."""

from pyspark.sql.types import (
    BooleanType,
    DateType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from lakehouse_jobs.common.s3 import read_bytes
from lakehouse_platform.contracts import (
    DataContracts,
    ManagedIcebergTableContract,
    TableIdentifier,
)

SPARK_TYPES = {
    "boolean": BooleanType(),
    "date": DateType(),
    "long": LongType(),
    "string": StringType(),
    "timestamptz": TimestampType(),
}


def load_contracts(uri: str) -> DataContracts:
    return DataContracts.model_validate_json(read_bytes(uri))


def spark_identifier(catalog_name: str, identifier: TableIdentifier) -> str:
    return ".".join((catalog_name, *identifier.iceberg))


def spark_schema(table: ManagedIcebergTableContract) -> StructType:
    return StructType(
        [
            StructField(
                column.name,
                SPARK_TYPES[column.data_type],
                nullable=not column.required,
            )
            for column in table.columns
        ]
    )
