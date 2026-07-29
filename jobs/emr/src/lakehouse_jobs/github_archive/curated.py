"""Publish canonical GitHub entities from a bounded landing partition."""

from pyspark.sql import SparkSession

from lakehouse_jobs.common.contracts import spark_identifier
from lakehouse_platform.contracts.curated import CuratedProductContract


def publish(
    spark: SparkSession,
    *,
    catalog_name: str,
    landing_table: str,
    product: CuratedProductContract,
    source_date: str,
) -> None:
    events = spark_identifier(catalog_name, product.table_identifier("events"))
    actors = spark_identifier(catalog_name, product.table_identifier("actors_current"))
    repositories = spark_identifier(
        catalog_name,
        product.table_identifier("repositories_current"),
    )
    spark.sql(
        f"""
        CREATE OR REPLACE TEMP VIEW github_day AS
        SELECT
            event_id,
            event_type,
            actor_id,
            nullif(trim(actor_login), '') AS actor_login,
            repository_id,
            nullif(trim(repository_name), '') AS repository_name,
            is_public,
            occurred_at,
            CAST(occurred_at AS date) AS event_date_utc,
            try_cast(get_json_object(payload_json, '$.size') AS bigint) AS push_commit_count,
            get_json_object(payload_json, '$.action') AS event_action,
            get_json_object(payload_json, '$.ref_type') AS ref_type,
            try_cast(get_json_object(payload_json, '$.issue.number') AS bigint) AS issue_number,
            try_cast(
                get_json_object(payload_json, '$.pull_request.number') AS bigint
            ) AS pull_request_number,
            try_cast(get_json_object(payload_json, '$.comment.id') AS bigint) AS comment_id,
            source_file,
            source_hour,
            ingested_at,
            current_timestamp() AS curated_at
        FROM {landing_table}
        WHERE CAST(source_hour AS date) = DATE '{source_date}'
        """
    )
    spark.sql(
        f"""
        MERGE INTO {events} target
        USING github_day source
        ON target.event_id = source.event_id
        WHEN MATCHED AND struct(
            source.source_hour, source.ingested_at, source.source_file
        ) > struct(
            target.source_hour, target.ingested_at, target.source_file
        ) THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
        """
    )
    spark.sql(
        """
        CREATE OR REPLACE TEMP VIEW github_actors AS
        SELECT
            actor_id,
            max_by(actor_login, struct(occurred_at, ingested_at, event_id)) AS actor_login,
            lower(coalesce(
                max_by(actor_login, struct(occurred_at, ingested_at, event_id)), ''
            )) rlike '(\\[bot\\]|-bot)$' AS is_bot,
            max(occurred_at) AS last_observed_at,
            current_timestamp() AS curated_at
        FROM github_day
        WHERE actor_id IS NOT NULL
        GROUP BY actor_id
        """
    )
    spark.sql(
        f"""
        MERGE INTO {actors} target
        USING github_actors source
        ON target.actor_id = source.actor_id
        WHEN MATCHED AND (
            source.last_observed_at > target.last_observed_at
            OR (
                source.last_observed_at = target.last_observed_at
                AND (
                    NOT (source.actor_login <=> target.actor_login)
                    OR source.is_bot <> target.is_bot
                )
            )
        ) THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
        """
    )
    spark.sql(
        """
        CREATE OR REPLACE TEMP VIEW github_repositories AS
        SELECT
            repository_id,
            max_by(repository_name, struct(occurred_at, ingested_at, event_id))
                AS repository_name,
            split(
                max_by(repository_name, struct(occurred_at, ingested_at, event_id)),
                '/'
            )[0] AS repository_owner,
            max(occurred_at) AS last_observed_at,
            current_timestamp() AS curated_at
        FROM github_day
        WHERE repository_id IS NOT NULL
        GROUP BY repository_id
        """
    )
    spark.sql(
        f"""
        MERGE INTO {repositories} target
        USING github_repositories source
        ON target.repository_id = source.repository_id
        WHEN MATCHED AND (
            source.last_observed_at > target.last_observed_at
            OR (
                source.last_observed_at = target.last_observed_at
                AND (
                    NOT (source.repository_name <=> target.repository_name)
                    OR NOT (source.repository_owner <=> target.repository_owner)
                )
            )
        ) THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
        """
    )
