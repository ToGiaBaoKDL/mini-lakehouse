from collections.abc import Sequence
from datetime import datetime
from typing import Any

import pytest

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.platform.trino import QueryResult
from mini_lakehouse.products.github.schema import TABLE_SPECS
from mini_lakehouse.products.github.service import GithubCurationService
from mini_lakehouse.sources.github_archive.models import ArchiveHour


class FakeTrinoExecutor:
    def __init__(self, source_hours: tuple[tuple[datetime, int], ...]) -> None:
        self.source_hours = source_hours
        self.calls: list[tuple[str, tuple[Any, ...] | None]] = []

    def execute(
        self,
        statement: str,
        parameters: Sequence[Any] | None = None,
    ) -> QueryResult:
        bound_parameters = tuple(parameters) if parameters is not None else None
        self.calls.append((statement, bound_parameters))
        normalized = statement.strip()
        if normalized.startswith("DESCRIBE"):
            table_key = normalized.rsplit('"', 2)[1]
            rows = tuple((name, data_type) for name, data_type, _ in TABLE_SPECS[table_key].columns)
            return QueryResult(columns=("Column", "Type"), rows=rows)
        if normalized.startswith("SHOW CREATE TABLE"):
            table_key = normalized.rsplit('"', 2)[1]
            spec = TABLE_SPECS[table_key]
            partitioning = " ".join(f"'{value}'" for value in spec.partitioning)
            ddl = f"location = 's3://curated/github/{table_key}' {partitioning}"
            return QueryResult(columns=("Create Table",), rows=((ddl,),))
        if "GROUP BY source_hour" in statement:
            return QueryResult(
                columns=("source_hour", "row_count"),
                rows=self.source_hours,
            )
        if "AS event_rows" in statement:
            return QueryResult(
                columns=("event_rows", "actor_rows", "repository_rows"),
                rows=((17, 5, 8),),
            )
        return QueryResult(columns=(), rows=())


def test_curation_is_bounded_to_one_source_hour() -> None:
    source_hour = ArchiveHour.parse("2025-01-02T03:00:00Z")
    executor = FakeTrinoExecutor(((source_hour.value, 10),))

    result = GithubCurationService(Settings(), executor=executor).curate(source_hour)

    assert result.source_hour == source_hour.value
    assert result.source_rows == 10
    assert (result.event_rows, result.actor_rows, result.repository_rows) == (17, 5, 8)
    merge_calls = [call for call in executor.calls if call[0].lstrip().startswith("MERGE INTO")]
    assert len(merge_calls) == 3
    assert merge_calls[0][1] == (source_hour.value,)
    assert all(
        parameters == (source_hour.value.date(), source_hour.value)
        for _, parameters in merge_calls[1:]
    )
    metrics = next(call for call in executor.calls if "AS event_rows" in call[0])
    assert metrics[1] == (source_hour.value.date(), source_hour.value)


def test_curation_rejects_a_missing_landing_hour() -> None:
    source_hour = ArchiveHour.parse("2025-01-02T04:00:00Z")
    executor = FakeTrinoExecutor(())

    with pytest.raises(RuntimeError, match="2025-01-02T04:00:00Z"):
        GithubCurationService(Settings(), executor=executor).curate(source_hour)

    assert not any(call[0].lstrip().startswith("MERGE INTO") for call in executor.calls)
