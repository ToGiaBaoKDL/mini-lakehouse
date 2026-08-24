"""Iceberg catalog naming and table guards shared by Spark jobs."""

from pyspark.sql import SparkSession

from lakehouse.catalog import CATALOG_NAME
from lakehouse.contracts import TableIdentifier


def qualified_name(identifier: TableIdentifier) -> str:
    return ".".join((CATALOG_NAME, *identifier.iceberg))


def require_tables(
    spark: SparkSession,
    identifiers: tuple[TableIdentifier, ...],
) -> None:
    tables = tuple(qualified_name(identifier) for identifier in identifiers)
    missing = [table for table in tables if not spark.catalog.tableExists(table)]
    if missing:
        raise RuntimeError(
            "Missing contract-managed Iceberg tables; run catalog apply first: "
            + ", ".join(missing)
        )
