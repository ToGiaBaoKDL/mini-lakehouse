from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import PlatformContracts, load_contracts
from mini_lakehouse.platform.trino import SqlExecutor, TrinoExecutor
from mini_lakehouse.products.github.schema import TABLE_SPECS, CuratedTableSpec
from mini_lakehouse.sources.github_archive.models import ArchiveHour


class GithubCurationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_hour: datetime
    source_rows: int
    event_rows: int
    actor_rows: int
    repository_rows: int


class GithubCurationService:
    def __init__(
        self,
        settings: Settings,
        *,
        executor: SqlExecutor | None = None,
        contracts: PlatformContracts | None = None,
    ) -> None:
        self._settings = settings
        self._contracts = contracts or load_contracts(settings.contracts_dir)
        self._source = self._contracts.source("github_archive")
        self._product = self._contracts.product("github")
        if self._product.upstream_sources != (self._source.name,):
            raise ValueError("GitHub curation requires github_archive as its only upstream source")
        contract_table_keys = {table.key for table in self._product.tables}
        if contract_table_keys != set(TABLE_SPECS):
            raise ValueError(
                "GitHub product tables and runtime schemas differ; "
                f"contract={sorted(contract_table_keys)!r}, runtime={sorted(TABLE_SPECS)!r}"
            )
        self._executor = executor

    def curate(self, source_hour: ArchiveHour) -> GithubCurationResult:
        owned_executor = TrinoExecutor(self._settings.trino) if self._executor is None else None
        context = owned_executor if owned_executor is not None else nullcontext(self._executor)
        with context as executor:
            if executor is None:
                raise RuntimeError("A SQL executor is required")
            self._ensure_tables(executor)
            source_rows = self._validate_source_hour(executor, source_hour)
            executor.execute(self._merge_events_sql(), (source_hour.value,))
            hour_parameters = (source_hour.value.date(), source_hour.value)
            executor.execute(self._merge_actors_sql(), hour_parameters)
            executor.execute(self._merge_repositories_sql(), hour_parameters)
            counts = self._hour_counts(executor, source_hour)
        return GithubCurationResult(
            source_hour=source_hour.value,
            source_rows=source_rows,
            event_rows=counts["events"],
            actor_rows=counts["actors_current"],
            repository_rows=counts["repositories_current"],
        )

    def _relation(self, table_key: str) -> str:
        return self._product.table_identifier(table_key).trino(self._settings.trino.catalog)

    def _source_relation(self) -> str:
        return self._source.table_identifier("events_raw").trino(self._settings.trino.catalog)

    def _table_location(self, table_key: str) -> str:
        table = self._product.table(table_key)
        return f"{self._settings.storage.curated_uri.rstrip('/')}/{table.location_prefix}"

    def _ensure_tables(self, executor: SqlExecutor) -> None:
        for table_key, spec in TABLE_SPECS.items():
            table_contract = self._product.table(table_key)
            if table_contract.schema_contract != spec.schema_contract:
                raise ValueError(
                    f"Table {table_contract.name!r} references {table_contract.schema_contract!r}; "
                    f"expected {spec.schema_contract!r}"
                )
            if table_contract.primary_key != spec.primary_key:
                raise ValueError(
                    f"Table {table_contract.name!r} primary key is "
                    f"{table_contract.primary_key!r}; expected {spec.primary_key!r}"
                )
            contract_partitioning = tuple(
                f"{partition.transform}({partition.field})"
                if partition.transform != "identity"
                else partition.field
                for partition in table_contract.partitioning
            )
            if contract_partitioning != spec.partitioning:
                raise ValueError(
                    f"Table {table_contract.name!r} partitioning is "
                    f"{contract_partitioning!r}; expected {spec.partitioning!r}"
                )
            executor.execute(self._create_table_sql(table_key, spec))
            self._validate_table(executor, table_key, spec)

    def _create_table_sql(self, table_key: str, spec: CuratedTableSpec) -> str:
        columns = ",\n    ".join(
            f'"{name}" {data_type}{" NOT NULL" if required else ""}'
            for name, data_type, required in spec.columns
        )
        properties = [
            "format = 'PARQUET'",
            "format_version = 2",
            f"location = '{self._table_location(table_key)}'",
        ]
        if spec.partitioning:
            values = ", ".join(f"'{value}'" for value in spec.partitioning)
            properties.append(f"partitioning = ARRAY[{values}]")
        return (
            f"CREATE TABLE IF NOT EXISTS {self._relation(table_key)} (\n"
            f"    {columns}\n"
            ")\nWITH (\n    " + ",\n    ".join(properties) + "\n)"
        )

    def _validate_table(
        self,
        executor: SqlExecutor,
        table_key: str,
        spec: CuratedTableSpec,
    ) -> None:
        relation = self._relation(table_key)
        description = executor.execute(f"DESCRIBE {relation}")
        actual_columns = tuple((str(row[0]), str(row[1]).lower()) for row in description.rows)
        expected_columns = tuple((name, data_type) for name, data_type, _ in spec.columns)
        if actual_columns != expected_columns:
            raise RuntimeError(
                f"Curated table {relation} schema drifted; expected {expected_columns!r}, "
                f"found {actual_columns!r}"
            )
        create_statement = executor.execute(f"SHOW CREATE TABLE {relation}")
        if len(create_statement.rows) != 1:
            raise RuntimeError(f"Trino returned no canonical DDL for {relation}")
        ddl = str(create_statement.rows[0][0])
        expected_location = f"location = '{self._table_location(table_key)}'"
        if expected_location not in ddl:
            raise RuntimeError(
                f"Curated table {relation} has a non-canonical location; "
                f"expected {self._table_location(table_key)!r}"
            )
        for partition in spec.partitioning:
            if f"'{partition}'" not in ddl:
                raise RuntimeError(
                    f"Curated table {relation} is missing partition transform {partition!r}"
                )

    def _validate_source_hour(
        self,
        executor: SqlExecutor,
        source_hour: ArchiveHour,
    ) -> int:
        result = executor.execute(
            f"""
            SELECT source_hour, count(*) AS row_count
            FROM {self._source_relation()}
            WHERE source_hour = ?
            GROUP BY source_hour
            """,
            (source_hour.value,),
        )
        actual = {self._utc(row[0]): int(row[1]) for row in result.rows}
        if source_hour.value not in actual:
            formatted = source_hour.value.isoformat().replace("+00:00", "Z")
            raise RuntimeError(f"Landing is missing GitHub Archive hour: {formatted}")
        return actual[source_hour.value]

    @staticmethod
    def _utc(value: Any) -> datetime:
        if not isinstance(value, datetime):
            raise TypeError(f"Expected Trino timestamp, got {type(value).__name__}")
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _hour_source_sql(self) -> str:
        return f"""
            SELECT
                event_id,
                event_type,
                actor_id,
                nullif(trim(actor_login), '') AS actor_login,
                repository_id,
                nullif(trim(repository_name), '') AS repository_name,
                is_public,
                occurred_at,
                CAST(occurred_at AT TIME ZONE 'UTC' AS date) AS event_date_utc,
                try_cast(json_extract_scalar(payload_json, '$.size') AS bigint)
                    AS push_commit_count,
                json_extract_scalar(payload_json, '$.action') AS event_action,
                json_extract_scalar(payload_json, '$.ref_type') AS ref_type,
                try_cast(json_extract_scalar(payload_json, '$.issue.number') AS bigint)
                    AS issue_number,
                try_cast(json_extract_scalar(payload_json, '$.pull_request.number') AS bigint)
                    AS pull_request_number,
                try_cast(json_extract_scalar(payload_json, '$.comment.id') AS bigint)
                    AS comment_id,
                source_file,
                source_hour,
                ingested_at,
                row_number() OVER (
                    PARTITION BY event_id
                    ORDER BY ingested_at DESC, source_hour DESC, source_file DESC
                ) AS ingestion_rank
            FROM {self._source_relation()}
            WHERE source_hour = ?
        """

    def _merge_events_sql(self) -> str:
        target = self._relation("events")
        columns = [name for name, _, _ in TABLE_SPECS["events"].columns]
        source_columns = [column for column in columns if column != "curated_at"]
        update_columns = [column for column in source_columns if column != "event_id"]
        update_assignments = ",\n                ".join(
            f'"{column}" = source."{column}"' for column in update_columns
        )
        insert_columns = ", ".join(f'"{column}"' for column in columns)
        insert_values = ", ".join(
            "current_timestamp" if column == "curated_at" else f'source."{column}"'
            for column in columns
        )
        return f"""
            MERGE INTO {target} AS target
            USING (
                SELECT {", ".join(source_columns)}
                FROM ({self._hour_source_sql()})
                WHERE ingestion_rank = 1
            ) AS source
            ON target.event_id = source.event_id
               AND target.event_date_utc = source.event_date_utc
            WHEN MATCHED AND source.ingested_at > target.ingested_at THEN
                UPDATE SET
                {update_assignments},
                "curated_at" = current_timestamp
            WHEN NOT MATCHED THEN
                INSERT ({insert_columns})
                VALUES ({insert_values})
        """

    def _merge_actors_sql(self) -> str:
        return f"""
            MERGE INTO {self._relation("actors_current")} AS target
            USING (
                WITH latest AS (
                    SELECT
                        actor_id,
                        max_by(actor_login, occurred_at) FILTER (WHERE actor_login IS NOT NULL)
                            AS actor_login,
                        max(occurred_at) AS last_observed_at
                    FROM {self._relation("events")}
                    WHERE event_date_utc = ?
                      AND source_hour = ?
                      AND actor_id IS NOT NULL
                    GROUP BY actor_id
                )
                SELECT
                    actor_id,
                    actor_login,
                    ends_with(lower(coalesce(actor_login, '')), '[bot]')
                        OR ends_with(lower(coalesce(actor_login, '')), '-bot') AS is_bot,
                    last_observed_at
                FROM latest
            ) AS source
            ON target.actor_id = source.actor_id
            WHEN MATCHED AND (
                source.last_observed_at > target.last_observed_at
                OR (
                    source.last_observed_at = target.last_observed_at
                    AND (
                        source.actor_login IS DISTINCT FROM target.actor_login
                        OR source.is_bot IS DISTINCT FROM target.is_bot
                    )
                )
            ) THEN
                UPDATE SET
                    "actor_login" = source.actor_login,
                    "is_bot" = source.is_bot,
                    "last_observed_at" = source.last_observed_at,
                    "curated_at" = current_timestamp
            WHEN NOT MATCHED THEN
                INSERT (actor_id, actor_login, is_bot, last_observed_at, curated_at)
                VALUES (
                    source.actor_id,
                    source.actor_login,
                    source.is_bot,
                    source.last_observed_at,
                    current_timestamp
                )
        """

    def _merge_repositories_sql(self) -> str:
        return f"""
            MERGE INTO {self._relation("repositories_current")} AS target
            USING (
                WITH latest AS (
                    SELECT
                        repository_id,
                        max_by(repository_name, occurred_at)
                            FILTER (WHERE repository_name IS NOT NULL) AS repository_name,
                        max(occurred_at) AS last_observed_at
                    FROM {self._relation("events")}
                    WHERE event_date_utc = ?
                      AND source_hour = ?
                      AND repository_id IS NOT NULL
                    GROUP BY repository_id
                )
                SELECT
                    repository_id,
                    repository_name,
                    split_part(repository_name, '/', 1) AS repository_owner,
                    last_observed_at
                FROM latest
            ) AS source
            ON target.repository_id = source.repository_id
            WHEN MATCHED AND (
                source.last_observed_at > target.last_observed_at
                OR (
                    source.last_observed_at = target.last_observed_at
                    AND (
                        source.repository_name IS DISTINCT FROM target.repository_name
                        OR source.repository_owner IS DISTINCT FROM target.repository_owner
                    )
                )
            ) THEN
                UPDATE SET
                    "repository_name" = source.repository_name,
                    "repository_owner" = source.repository_owner,
                    "last_observed_at" = source.last_observed_at,
                    "curated_at" = current_timestamp
            WHEN NOT MATCHED THEN
                INSERT (
                    repository_id,
                    repository_name,
                    repository_owner,
                    last_observed_at,
                    curated_at
                )
                VALUES (
                    source.repository_id,
                    source.repository_name,
                    source.repository_owner,
                    source.last_observed_at,
                    current_timestamp
                )
        """

    def _hour_counts(
        self,
        executor: SqlExecutor,
        source_hour: ArchiveHour,
    ) -> dict[str, int]:
        result = executor.execute(
            f"""
            SELECT
                count(*) AS event_rows,
                count(DISTINCT actor_id) FILTER (WHERE actor_id IS NOT NULL) AS actor_rows,
                count(DISTINCT repository_id) FILTER (WHERE repository_id IS NOT NULL)
                    AS repository_rows
            FROM {self._relation("events")}
            WHERE event_date_utc = ?
              AND source_hour = ?
            """,
            (source_hour.value.date(), source_hour.value),
        )
        if len(result.rows) != 1:
            raise RuntimeError("Trino returned no curation metrics")
        row = result.rows[0]
        return {
            "events": int(row[0]),
            "actors_current": int(row[1]),
            "repositories_current": int(row[2]),
        }
