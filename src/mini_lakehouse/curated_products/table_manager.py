"""Physical table lifecycle for curated product contracts."""

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import (
    PlatformContracts,
    load_contracts,
    partition_expression,
    trino_type,
)
from mini_lakehouse.contracts.curated_products import CuratedTableContract
from mini_lakehouse.platform.runtime import namespace_table_storage_uri
from mini_lakehouse.platform.trino import SqlExecutor


class CuratedTableManager:
    def __init__(
        self,
        settings: Settings,
        product_name: str,
        contracts: PlatformContracts | None = None,
    ) -> None:
        self._settings = settings
        self._product = (contracts or load_contracts(settings.contracts_dir)).curated_product(
            product_name
        )

    def _relation(self, table: CuratedTableContract) -> str:
        return self._product.table_identifier(table.key).trino(self._settings.trino.catalog)

    def _location(self, table: CuratedTableContract) -> str:
        return namespace_table_storage_uri(
            self._settings,
            self._product.curated_namespace,
            table.name,
        )

    def ensure_tables(self, executor: SqlExecutor) -> None:
        for table in self._product.tables:
            executor.execute(self._create_table_sql(table))
            self._validate_table(executor, table)

    def _create_table_sql(self, table: CuratedTableContract) -> str:
        columns = ",\n    ".join(
            f'"{column.name}" {trino_type(column)}{" NOT NULL" if column.required else ""}'
            for column in table.columns
        )
        properties = [
            "format = 'PARQUET'",
            "format_version = 2",
        ]
        if table.partitioning:
            values = ", ".join(
                f"'{partition_expression(partition)}'" for partition in table.partitioning
            )
            properties.append(f"partitioning = ARRAY[{values}]")
        return (
            f"CREATE TABLE IF NOT EXISTS {self._relation(table)} (\n"
            f"    {columns}\n"
            ")\nWITH (\n    " + ",\n    ".join(properties) + "\n)"
        )

    def _validate_table(self, executor: SqlExecutor, table: CuratedTableContract) -> None:
        relation = self._relation(table)
        description = executor.execute(f"DESCRIBE {relation}")
        actual_columns = tuple((str(row[0]), str(row[1]).lower()) for row in description.rows)
        expected_columns = tuple((column.name, trino_type(column)) for column in table.columns)
        if actual_columns != expected_columns:
            raise RuntimeError(
                f"Curated table {relation} schema drifted; expected {expected_columns!r}, "
                f"found {actual_columns!r}"
            )
        create_statement = executor.execute(f"SHOW CREATE TABLE {relation}")
        if len(create_statement.rows) != 1:
            raise RuntimeError(f"Trino returned no canonical DDL for {relation}")
        ddl = str(create_statement.rows[0][0])
        expected_location = f"location = '{self._location(table)}'"
        if expected_location not in ddl:
            raise RuntimeError(
                f"Curated table {relation} location drifted; expected {self._location(table)!r}"
            )
        for partition in table.partitioning:
            expression = partition_expression(partition)
            if f"'{expression}'" not in ddl:
                raise RuntimeError(
                    f"Curated table {relation} is missing partition expression {expression!r}"
                )
