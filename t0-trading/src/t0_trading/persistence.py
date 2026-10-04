"""Minimal PostgreSQL transaction interface shared by paper and shadow delivery."""

from contextlib import AbstractContextManager
from typing import Protocol


class TransientDatabaseError(RuntimeError):
    """Temporary connectivity/transaction failure; retry only an idempotent operation."""


class PostgresCursor(Protocol):
    rowcount: int

    def fetchone(self) -> tuple[object, ...] | None: ...
    def fetchall(self) -> list[tuple[object, ...]]: ...


class PostgresConnection(Protocol):
    autocommit: bool

    def transaction(self) -> AbstractContextManager[object]: ...
    def execute(self, query: str, params: tuple[object, ...]) -> PostgresCursor: ...
