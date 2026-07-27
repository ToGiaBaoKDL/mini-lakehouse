from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

import pytest

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.curated.github.service import GithubCurationService
from mini_lakehouse.platform.trino import QueryResult
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
        if "GROUP BY source_hour" in statement:
            return QueryResult(
                columns=("source_hour", "source_event_date", "row_count"),
                rows=tuple(
                    (source_hour, source_hour.date(), row_count)
                    for source_hour, row_count in self.source_hours
                ),
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
    event_merge = merge_calls[0][0]
    assert 'ON target."event_id" = source."event_id"' in event_merge
    assert "target.event_date_utc = source.event_date_utc" not in event_merge
    assert "source.ingested_at," in event_merge
    assert "source.source_hour," in event_merge
    assert "source.source_file" in event_merge
    assert not any(call[0].startswith("CREATE TABLE") for call in executor.calls)


def test_curation_rejects_a_missing_landing_hour() -> None:
    source_hour = ArchiveHour.parse("2025-01-02T04:00:00Z")
    executor = FakeTrinoExecutor(())

    with pytest.raises(RuntimeError, match="2025-01-02T04:00:00Z"):
        GithubCurationService(Settings(), executor=executor).curate(source_hour)

    assert not any(call[0].lstrip().startswith("MERGE INTO") for call in executor.calls)


def test_curation_bounds_late_events_by_their_actual_event_dates() -> None:
    source_hour = ArchiveHour.parse("2025-01-02T03:00:00Z")
    event_dates = (date(2024, 12, 31), date(2025, 1, 1))
    executor = FakeTrinoExecutor(())

    def execute(
        statement: str,
        parameters: Sequence[Any] | None = None,
    ) -> QueryResult:
        if "GROUP BY source_hour" in statement:
            executor.calls.append((statement, tuple(parameters or ())))
            return QueryResult(
                columns=("source_hour", "source_event_date", "row_count"),
                rows=(
                    (source_hour.value, event_dates[0], 4),
                    (source_hour.value, event_dates[1], 6),
                ),
            )
        return FakeTrinoExecutor.execute(executor, statement, parameters)

    executor.execute = execute  # type: ignore[method-assign]

    result = GithubCurationService(Settings(), executor=executor).curate(source_hour)

    assert result.source_rows == 10
    entity_merges = [
        call
        for call in executor.calls
        if call[0].lstrip().startswith("MERGE INTO") and 'events" AS target' not in call[0]
    ]
    assert [parameters for _, parameters in entity_merges] == [
        (event_dates[0], source_hour.value),
        (event_dates[0], source_hour.value),
        (event_dates[1], source_hour.value),
        (event_dates[1], source_hour.value),
    ]
    metrics = next(call for call in executor.calls if "AS event_rows" in call[0])
    assert metrics[1] == (*event_dates, source_hour.value)
