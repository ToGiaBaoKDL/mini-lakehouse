"""Validated declarative contracts shared across platform boundaries."""

from mini_lakehouse.contracts.identifiers import TableIdentifier
from mini_lakehouse.contracts.loader import load_contracts
from mini_lakehouse.contracts.registry import PlatformContracts

__all__ = [
    "PlatformContracts",
    "TableIdentifier",
    "load_contracts",
]
