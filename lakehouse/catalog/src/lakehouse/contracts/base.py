"""Shared primitives for declarative data contracts."""

import re
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier: TypeAlias = Annotated[  # noqa: UP040
    str,
    StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"),
]
ContractName: TypeAlias = Annotated[  # noqa: UP040
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]
LogicalType: TypeAlias = Literal[  # noqa: UP040
    "string", "long", "boolean", "timestamptz", "date", "double", "decimal"
]

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
    precision: int | None = Field(default=None, ge=1, le=38)
    scale: int | None = Field(default=None, ge=0, le=38)
    required: bool
    description: str

    @model_validator(mode="after")
    def validate_decimal(self) -> "ColumnContract":
        if self.data_type == "decimal":
            if self.precision is None or self.scale is None:
                raise ValueError("Decimal columns require precision and scale")
            if self.scale > self.precision:
                raise ValueError("Decimal scale cannot exceed precision")
        elif self.precision is not None or self.scale is not None:
            raise ValueError("Precision and scale are valid only for decimal columns")
        return self


def validate_relative_prefix(value: str) -> str:
    if any(segment in {"", ".", ".."} for segment in value.split("/")):
        raise ValueError("Object prefixes must be normalized relative paths")
    if not _SAFE_PREFIX.fullmatch(value):
        raise ValueError(f"Unsafe object prefix: {value!r}")
    return value
