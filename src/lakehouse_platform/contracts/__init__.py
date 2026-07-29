"""Validated declarative data contracts."""

from lakehouse_platform.contracts.loader import load_contracts
from lakehouse_platform.contracts.registry import DataContracts
from lakehouse_platform.contracts.tables import (
    ManagedIcebergTableContract,
    TableIdentifier,
)

__all__ = [
    "DataContracts",
    "ManagedIcebergTableContract",
    "TableIdentifier",
    "load_contracts",
]
