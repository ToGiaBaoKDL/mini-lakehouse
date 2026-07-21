"""Validated declarative contracts shared across platform boundaries."""

from mini_lakehouse.contracts.identifiers import TableIdentifier
from mini_lakehouse.contracts.loader import load_contracts
from mini_lakehouse.contracts.registry import PlatformContracts
from mini_lakehouse.contracts.schema import (
    arrow_schema,
    iceberg_schema,
    partition_expression,
    partition_spec,
    trino_type,
)

__all__ = [
    "PlatformContracts",
    "TableIdentifier",
    "arrow_schema",
    "iceberg_schema",
    "load_contracts",
    "partition_expression",
    "partition_spec",
    "trino_type",
]
