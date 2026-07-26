from collections.abc import Sequence
from typing import Any

import pytest

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import load_contracts, trino_type
from mini_lakehouse.contracts.base import ColumnContract
from mini_lakehouse.contracts.curated_products import CuratedTableContract
from mini_lakehouse.curated_products.table_manager import CuratedTableManager
from mini_lakehouse.platform.trino import QueryResult, SqlExecutor


class _TestTableManager(CuratedTableManager):
    def create_table_sql(self, table: CuratedTableContract) -> str:
        return self._create_table_sql(table)

    def validate_table(
        self,
        executor: SqlExecutor,
        table: CuratedTableContract,
    ) -> None:
        self._validate_table(executor, table)


class _SchemaExecutor:
    def __init__(
        self,
        columns: tuple[tuple[str, str], ...],
        *,
        expected_location: str,
        include_retention: bool = True,
    ) -> None:
        self.columns = list(columns)
        self.expected_location = expected_location
        self.include_retention = include_retention
        self.calls: list[str] = []

    def execute(
        self,
        statement: str,
        parameters: Sequence[Any] | None = None,
    ) -> QueryResult:
        del parameters
        self.calls.append(statement)
        if statement.startswith("DESCRIBE"):
            return QueryResult(columns=("Column", "Type"), rows=tuple(self.columns))
        if statement.startswith("ALTER TABLE") and " ADD COLUMN " in statement:
            self.columns.append(("job_json", "varchar"))
            return QueryResult(columns=(), rows=())
        if statement.startswith("SHOW CREATE TABLE"):
            retention = (
                "\ndelete_after_commit_enabled = true,\nmax_previous_versions = 30"
                if self.include_retention
                else ""
            )
            return QueryResult(
                columns=("Create Table",),
                rows=((f"location = '{self.expected_location}'{retention}",),),
            )
        return QueryResult(columns=(), rows=())


def test_curated_table_manager_adds_only_trailing_nullable_columns() -> None:
    contracts = load_contracts()
    table = contracts.curated_product("arxiv").table("ocr_batches")
    manager = _TestTableManager(Settings(), "arxiv", contracts)
    existing = tuple(
        (column.name, trino_type(column)) for column in table.columns if column.name != "job_json"
    )
    executor = _SchemaExecutor(
        existing,
        expected_location="s3://curated/arxiv/ocr_batches",
    )

    manager.validate_table(executor, table)

    alter = [statement for statement in executor.calls if statement.startswith("ALTER TABLE")]
    assert alter == [
        'ALTER TABLE "prod"."curated.arxiv"."ocr_batches" ADD COLUMN "job_json" varchar'
    ]


def test_curated_table_manager_rejects_a_required_additive_column() -> None:
    contracts = load_contracts()
    table = contracts.curated_product("arxiv").table("ocr_batches")
    required_column = ColumnContract(
        field_id=14,
        name="required_later",
        data_type="string",
        required=True,
        description="Unsafe required schema change.",
    )
    changed = table.model_copy(update={"columns": (*table.columns, required_column)})
    manager = _TestTableManager(Settings(), "arxiv", contracts)
    existing = tuple((column.name, trino_type(column)) for column in table.columns)
    executor = _SchemaExecutor(
        existing,
        expected_location="s3://curated/arxiv/ocr_batches",
    )

    with pytest.raises(RuntimeError, match="schema drifted"):
        manager.validate_table(executor, changed)

    assert not any(statement.startswith("ALTER TABLE") for statement in executor.calls)


def test_curated_table_manager_rejects_existing_type_drift() -> None:
    contracts = load_contracts()
    table = contracts.curated_product("arxiv").table("ocr_batches")
    manager = _TestTableManager(Settings(), "arxiv", contracts)
    existing = tuple(
        (column.name, "bigint" if column.name == "batch_id" else trino_type(column))
        for column in table.columns
    )
    executor = _SchemaExecutor(
        existing,
        expected_location="s3://curated/arxiv/ocr_batches",
    )

    with pytest.raises(RuntimeError, match="schema drifted"):
        manager.validate_table(executor, table)

    assert not any(statement.startswith("ALTER TABLE") for statement in executor.calls)


def test_curated_table_ddl_and_reconciliation_share_metadata_retention() -> None:
    contracts = load_contracts()
    table = contracts.curated_product("arxiv").table("ocr_batches")
    manager = _TestTableManager(Settings(), "arxiv", contracts)
    columns = tuple((column.name, trino_type(column)) for column in table.columns)
    executor = _SchemaExecutor(
        columns,
        expected_location="s3://curated/arxiv/ocr_batches",
        include_retention=False,
    )

    ddl = manager.create_table_sql(table)
    manager.validate_table(executor, table)

    assert "delete_after_commit_enabled = true" in ddl
    assert "max_previous_versions = 30" in ddl
    assert any(
        statement.endswith(
            "SET PROPERTIES delete_after_commit_enabled = true, max_previous_versions = 30"
        )
        for statement in executor.calls
    )
