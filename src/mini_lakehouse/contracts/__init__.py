"""Validated declarative contracts shared across platform boundaries."""

from mini_lakehouse.contracts.loader import load_contracts
from mini_lakehouse.contracts.registry import PlatformContracts
from mini_lakehouse.contracts.tables import (
    ManagedIcebergTableContract,
    TableIdentifier,
    arrow_schema,
    iceberg_schema,
    partition_spec,
)

__all__ = [
    "ManagedIcebergTableContract",
    "PlatformContracts",
    "TableIdentifier",
    "arrow_schema",
    "iceberg_schema",
    "load_contracts",
    "partition_spec",
]
