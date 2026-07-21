from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, Self

import trino

from mini_lakehouse.config.settings import TrinoSettings


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


class SqlExecutor(Protocol):
    def execute(
        self,
        statement: str,
        parameters: Sequence[Any] | None = None,
    ) -> QueryResult: ...


class TrinoExecutor:
    def __init__(self, settings: TrinoSettings) -> None:
        self._connection = trino.dbapi.connect(
            host=settings.host,
            port=settings.port,
            user=settings.user,
            http_scheme=settings.http_scheme,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def execute(
        self,
        statement: str,
        parameters: Sequence[Any] | None = None,
    ) -> QueryResult:
        cursor = self._connection.cursor()
        try:
            if parameters is None:
                cursor.execute(statement)
            else:
                cursor.execute(statement, params=parameters)
            rows = tuple(tuple(row) for row in cursor.fetchall())
            columns = tuple(description[0] for description in cursor.description or ())
        finally:
            cursor.close()
        return QueryResult(columns=columns, rows=rows)
