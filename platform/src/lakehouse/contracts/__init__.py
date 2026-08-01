"""Validated declarative data contracts."""

from lakehouse.contracts.loader import load_contracts
from lakehouse.contracts.registry import DataContracts
from lakehouse.contracts.tables import (
    ManagedIcebergTableContract,
    TableIdentifier,
)

__all__ = [
    "DataContracts",
    "ManagedIcebergTableContract",
    "TableIdentifier",
    "load_contracts",
]
