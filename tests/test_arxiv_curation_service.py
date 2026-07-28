from collections.abc import Sequence
from datetime import date
from typing import Any

import pytest

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.curated.arxiv.repository import ArxivCurationRepository
from mini_lakehouse.platform.trino import QueryResult


class _FakeTrinoExecutor:
    def __init__(
        self,
        *,
        pending_rows: int = 1,
        fail_once_at_mutation: int | None = None,
    ) -> None:
        self.calls: list[tuple[str, tuple[Any, ...] | None]] = []
        self.pending_rows = pending_rows
        self.fail_once_at_mutation = fail_once_at_mutation
        self.mutation_count = 0

    def execute(
        self,
        statement: str,
        parameters: Sequence[Any] | None = None,
    ) -> QueryResult:
        self.calls.append(
            (statement, tuple(parameters) if parameters is not None else None),
        )
        mutation = statement.lstrip().startswith(("DELETE FROM", "INSERT INTO", "MERGE INTO"))
        if mutation:
            self.mutation_count += 1
            if self.mutation_count == self.fail_once_at_mutation:
                self.fail_once_at_mutation = None
                raise RuntimeError("simulated Trino mutation failure")
            if statement.lstrip().startswith("MERGE INTO"):
                self.pending_rows = 0
        if "SELECT count(*) FROM (" in statement and "LEFT JOIN" in statement:
            return QueryResult(columns=("_col0",), rows=((self.pending_rows,),))
        if "SELECT count(*)" in statement and "arxiv_oai_records_raw" in statement:
            return QueryResult(columns=("_col0",), rows=((1,),))
        if "SELECT record_count" in statement:
            return QueryResult(columns=("record_count",), rows=((1,),))
        if "AS papers" in statement:
            return QueryResult(
                columns=("papers", "authors", "categories"),
                rows=((1, 2, 2),),
            )
        return QueryResult(columns=(), rows=())


def test_author_unnest_aliases_every_row_field_and_ordinality() -> None:
    executor = _FakeTrinoExecutor()
    day = date(2026, 7, 22)

    ArxivCurationRepository(Settings()).curate_day(executor, day)

    author_insert = next(
        statement
        for statement, _ in executor.calls
        if statement.lstrip().startswith("INSERT INTO") and '"paper_authors"' in statement
    )
    assert "author_value" not in author_insert
    assert (
        """WITH ORDINALITY AS author(
                keyname,
                forenames,
                suffix,
                affiliation,
                author_position
            )"""
        in author_insert
    )
    assert "author.author_position" in author_insert
    assert "author.keyname" in author_insert

    mutations = [
        statement.lstrip().split(maxsplit=1)[0]
        for statement, _ in executor.calls
        if statement.lstrip().startswith(("DELETE FROM", "INSERT INTO", "MERGE INTO"))
    ]
    assert mutations == ["DELETE", "DELETE", "INSERT", "INSERT", "MERGE"]


def test_completed_arxiv_curation_retry_creates_no_mutation_statements() -> None:
    executor = _FakeTrinoExecutor(pending_rows=0)

    result = ArxivCurationRepository(Settings()).curate_day(
        executor,
        date(2026, 7, 22),
    )

    assert result["mutations"] == 0
    assert not any(
        statement.lstrip().startswith(("DELETE FROM", "INSERT INTO", "MERGE INTO"))
        for statement, _ in executor.calls
    )


@pytest.mark.parametrize("failure_point", range(1, 6))
def test_retry_converges_after_each_curation_publication_failure(
    failure_point: int,
) -> None:
    executor = _FakeTrinoExecutor(fail_once_at_mutation=failure_point)
    repository = ArxivCurationRepository(Settings())
    day = date(2026, 7, 22)

    with pytest.raises(RuntimeError, match="simulated Trino mutation failure"):
        repository.curate_day(executor, day)
    repaired = repository.curate_day(executor, day)
    completed = repository.curate_day(executor, day)

    mutations = [
        statement.lstrip().split(maxsplit=1)[0]
        for statement, _ in executor.calls
        if statement.lstrip().startswith(("DELETE FROM", "INSERT INTO", "MERGE INTO"))
    ]
    assert mutations[-5:] == ["DELETE", "DELETE", "INSERT", "INSERT", "MERGE"]
    assert repaired["mutations"] == 1
    assert completed["mutations"] == 0
