"""Bounded Trino mutations for the curated GitHub product."""

from datetime import UTC, date, datetime
from typing import Any

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import PlatformContracts, load_contracts
from mini_lakehouse.platform.trino import SqlExecutor
from mini_lakehouse.sources.github_archive.models import ArchiveHour


class GithubCurationRepository:
    def __init__(
        self,
        settings: Settings,
        contracts: PlatformContracts | None = None,
    ) -> None:
        self._settings = settings
        registry = contracts or load_contracts(settings.contracts_dir)
        self._source = registry.source("github_archive")
        self._product = registry.curated_product("github")

    def _relation(self, table_key: str) -> str:
        return self._product.table_identifier(table_key).trino(self._settings.trino.catalog)

    def _source_relation(self) -> str:
        return self._source.table_identifier("events_raw").trino(self._settings.trino.catalog)

    def curate_hour(
        self, executor: SqlExecutor, source_hour: ArchiveHour
    ) -> tuple[int, dict[str, int]]:
        source_rows, event_dates = self._validate_source_hour(executor, source_hour)
        executor.execute(self._merge_events_sql(), (source_hour.value,))
        for event_date in event_dates:
            hour_parameters = (event_date, source_hour.value)
            executor.execute(self._merge_actors_sql(), hour_parameters)
            executor.execute(self._merge_repositories_sql(), hour_parameters)
        return source_rows, self._hour_counts(executor, source_hour, event_dates)

    def _validate_source_hour(
        self,
        executor: SqlExecutor,
        source_hour: ArchiveHour,
    ) -> tuple[int, tuple[date, ...]]:
        result = executor.execute(
            f"""
            SELECT
                source_hour,
                CAST(occurred_at AT TIME ZONE 'UTC' AS date) AS source_event_date,
                count(*) AS row_count
            FROM {self._source_relation()}
            WHERE source_hour = ?
            GROUP BY source_hour, CAST(occurred_at AT TIME ZONE 'UTC' AS date)
            ORDER BY source_event_date
            """,
            (source_hour.value,),
        )
        rows = [row for row in result.rows if self._utc(row[0]) == source_hour.value]
        if not rows:
            formatted = source_hour.value.isoformat().replace("+00:00", "Z")
            raise RuntimeError(f"Landing is missing GitHub Archive hour: {formatted}")
        event_dates: list[date] = []
        for row in rows:
            if not isinstance(row[1], date):
                raise TypeError(f"Expected Trino date, got {type(row[1]).__name__}")
            event_dates.append(row[1])
        return sum(int(row[2]) for row in rows), tuple(event_dates)

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
        table = self._product.table("events")
        columns = [column.name for column in table.columns]
        source_columns = [column for column in columns if column != "curated_at"]
        update_columns = [column for column in source_columns if column not in table.primary_key]
        update_assignments = ",\n                ".join(
            f'"{column}" = source."{column}"' for column in update_columns
        )
        merge_key = " AND ".join(
            f'target."{column}" = source."{column}"' for column in table.primary_key
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
            ON {merge_key}
            WHEN MATCHED AND ROW(
                source.ingested_at,
                source.source_hour,
                source.source_file
            ) > ROW(
                target.ingested_at,
                target.source_hour,
                target.source_file
            ) THEN
                UPDATE SET
                {update_assignments},
                "curated_at" = current_timestamp
            WHEN NOT MATCHED THEN
                INSERT ({insert_columns})
                VALUES ({insert_values})
        """

    def _merge_current_entity_sql(self, table_key: str, source_sql: str) -> str:
        """Render shared current-state mechanics; callers retain business projections."""
        table = self._product.table(table_key)
        columns = [column.name for column in table.columns]
        source_columns = [column for column in columns if column != "curated_at"]
        mutable_columns = [column for column in source_columns if column not in table.primary_key]
        version_column = "last_observed_at"
        if version_column not in mutable_columns:
            raise ValueError(f"Current-state table {table.name!r} needs {version_column!r}")
        attribute_columns = [column for column in mutable_columns if column != version_column]
        merge_key = " AND ".join(
            f'target."{column}" = source."{column}"' for column in table.primary_key
        )
        changed_attributes = "\n                        OR ".join(
            f'source."{column}" IS DISTINCT FROM target."{column}"' for column in attribute_columns
        )
        update_assignments = ",\n                    ".join(
            f'"{column}" = source."{column}"' for column in mutable_columns
        )
        insert_columns = ", ".join(f'"{column}"' for column in columns)
        insert_values = ", ".join(
            "current_timestamp" if column == "curated_at" else f'source."{column}"'
            for column in columns
        )
        return f"""
            MERGE INTO {self._relation(table_key)} AS target
            USING (
                {source_sql}
            ) AS source
            ON {merge_key}
            WHEN MATCHED AND (
                source."{version_column}" > target."{version_column}"
                OR (
                    source."{version_column}" = target."{version_column}"
                    AND (
                        {changed_attributes}
                    )
                )
            ) THEN
                UPDATE SET
                    {update_assignments},
                    "curated_at" = current_timestamp
            WHEN NOT MATCHED THEN
                INSERT ({insert_columns})
                VALUES ({insert_values})
        """

    def _merge_actors_sql(self) -> str:
        source_sql = f"""
            WITH latest AS (
                SELECT
                    actor_id,
                    max_by(actor_login, ROW(occurred_at, ingested_at, event_id))
                        FILTER (WHERE actor_login IS NOT NULL) AS actor_login,
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
        """
        return self._merge_current_entity_sql("actors_current", source_sql)

    def _merge_repositories_sql(self) -> str:
        source_sql = f"""
            WITH latest AS (
                SELECT
                    repository_id,
                    max_by(repository_name, ROW(occurred_at, ingested_at, event_id))
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
        """
        return self._merge_current_entity_sql("repositories_current", source_sql)

    def _hour_counts(
        self,
        executor: SqlExecutor,
        source_hour: ArchiveHour,
        event_dates: tuple[date, ...],
    ) -> dict[str, int]:
        date_parameters = ", ".join("?" for _ in event_dates)
        result = executor.execute(
            f"""
            SELECT
                count(*) AS event_rows,
                count(DISTINCT actor_id) FILTER (WHERE actor_id IS NOT NULL) AS actor_rows,
                count(DISTINCT repository_id) FILTER (WHERE repository_id IS NOT NULL)
                    AS repository_rows
            FROM {self._relation("events")}
            WHERE event_date_utc IN ({date_parameters})
              AND source_hour = ?
            """,
            (*event_dates, source_hour.value),
        )
        if len(result.rows) != 1:
            raise RuntimeError("Trino returned no curation metrics")
        row = result.rows[0]
        return {
            "events": int(row[0]),
            "actors_current": int(row[1]),
            "repositories_current": int(row[2]),
        }
