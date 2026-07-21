import re
from dataclasses import dataclass

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(value: str) -> None:
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid catalog identifier: {value!r}")


def _quoted(value: str) -> str:
    _validate_identifier(value)
    return f'"{value}"'


@dataclass(frozen=True, slots=True)
class TableIdentifier:
    namespace: tuple[str, ...]
    name: str

    def __post_init__(self) -> None:
        if not self.namespace:
            raise ValueError("A table must belong to a namespace")
        for part in (*self.namespace, self.name):
            _validate_identifier(part)

    @classmethod
    def from_iceberg(cls, identifier: tuple[str, ...]) -> "TableIdentifier":
        if len(identifier) < 2:
            raise ValueError(f"Expected namespace and table name, got {identifier!r}")
        return cls(namespace=identifier[:-1], name=identifier[-1])

    @property
    def iceberg(self) -> tuple[str, ...]:
        return (*self.namespace, self.name)

    def trino(self, catalog: str) -> str:
        schema = ".".join(self.namespace)
        return ".".join((_quoted(catalog), f'"{schema}"', _quoted(self.name)))
