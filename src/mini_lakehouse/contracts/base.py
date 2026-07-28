import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

type Identifier = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"),
]
type ContractName = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]
type StorageTier = Literal["landing", "curated", "analytics"]
LIFECYCLE_TIERS: tuple[StorageTier, ...] = ("landing", "curated", "analytics")
type NamespacePath = tuple[Identifier, ...]
type LogicalType = Literal["string", "long", "boolean", "timestamptz", "date"]

_SAFE_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./=-]*$")


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContactContract(ContractModel):
    name: str
    email: Annotated[
        str,
        StringConstraints(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$"),
    ]


class ColumnContract(ContractModel):
    field_id: int
    name: Identifier
    data_type: LogicalType
    required: bool
    description: str


def validate_relative_prefix(value: str) -> str:
    if any(segment in {"", ".", ".."} for segment in value.split("/")):
        raise ValueError("Object prefixes must be normalized relative paths")
    if not _SAFE_PREFIX.fullmatch(value):
        raise ValueError(f"Unsafe object prefix: {value!r}")
    return value
