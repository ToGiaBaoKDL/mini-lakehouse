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
    spark.catalog.cacheTable("github_day")
    spark.sql(
        f"""
        MERGE INTO {events} target
        USING github_day source
        ON target.event_id = source.event_id
        WHEN MATCHED AND struct(
            source.source_hour, source.ingested_at, source.source_file
        ) > struct(
            target.source_hour, target.ingested_at, target.source_file
        ) THEN UPDATE SET
            event_type = source.event_type,
            actor_id = source.actor_id,
            actor_login = source.actor_login,
            repository_id = source.repository_id,
            repository_name = source.repository_name,
            is_public = source.is_public,
            occurred_at = source.occurred_at,
            event_date_utc = source.event_date_utc,
            push_commit_count = source.push_commit_count,
            event_action = source.event_action,
            ref_type = source.ref_type,
            issue_number = source.issue_number,
            pull_request_number = source.pull_request_number,
            comment_id = source.comment_id,
            source_file = source.source_file,
            source_hour = source.source_hour,
            ingested_at = source.ingested_at,
            curated_at = source.curated_at
        WHEN NOT MATCHED THEN INSERT (
            event_id,
            event_type,
            actor_id,
            actor_login,
            repository_id,
            repository_name,
            is_public,
            occurred_at,
            event_date_utc,
            push_commit_count,
            event_action,
            ref_type,
            issue_number,
            pull_request_number,
            comment_id,
            source_file,
            source_hour,
            ingested_at,
            curated_at
        ) VALUES (
            source.event_id,
            source.event_type,
            source.actor_id,
            source.actor_login,
            source.repository_id,
            source.repository_name,
            source.is_public,
            source.occurred_at,
            source.event_date_utc,
            source.push_commit_count,
            source.event_action,
            source.ref_type,
            source.issue_number,
            source.pull_request_number,
            source.comment_id,
            source.source_file,
            source.source_hour,
            source.ingested_at,
            source.curated_at
        )
        """
    )
    spark.sql(
        """
        CREATE OR REPLACE TEMP VIEW github_actors AS
        SELECT
            actor_id,
            winner.actor_login,
            lower(coalesce(winner.actor_login, '')) rlike '(\\[bot\\]|-bot)$' AS is_bot,
            winner.occurred_at AS last_observed_at,
            winner.event_id AS last_event_id,
            current_timestamp() AS curated_at
        FROM (
            SELECT
                actor_id,
                max_by(
                    named_struct(
                        'actor_login', actor_login,
                        'occurred_at', occurred_at,
                        'event_id', event_id
                    ),
                    struct(occurred_at, event_id)
                ) AS winner
            FROM github_day
            WHERE actor_id IS NOT NULL
            GROUP BY actor_id
        ) latest
        """
    )
    spark.sql(
        f"""
        MERGE INTO {actors} target
        USING github_actors source
        ON target.actor_id = source.actor_id
        WHEN MATCHED AND struct(
            source.last_observed_at, source.last_event_id
        ) > struct(
            target.last_observed_at, target.last_event_id
        ) THEN UPDATE SET
            actor_login = source.actor_login,
            is_bot = source.is_bot,
            last_observed_at = source.last_observed_at,
            last_event_id = source.last_event_id,
            curated_at = source.curated_at
        WHEN NOT MATCHED THEN INSERT (
            actor_id,
            actor_login,
            is_bot,
            last_observed_at,
            last_event_id,
            curated_at
        ) VALUES (
            source.actor_id,
            source.actor_login,
            source.is_bot,
            source.last_observed_at,
            source.last_event_id,
            source.curated_at
        )
        """
    )
    spark.sql(
        """
        CREATE OR REPLACE TEMP VIEW github_repositories AS
        SELECT
            repository_id,
            winner.repository_name,
            split(winner.repository_name, '/')[0] AS repository_owner,
            winner.occurred_at AS last_observed_at,
            winner.event_id AS last_event_id,
            current_timestamp() AS curated_at
        FROM (
            SELECT
                repository_id,
                max_by(
                    named_struct(
                        'repository_name', repository_name,
                        'occurred_at', occurred_at,
                        'event_id', event_id
                    ),
                    struct(occurred_at, event_id)
                ) AS winner
            FROM github_day
            WHERE repository_id IS NOT NULL
            GROUP BY repository_id
        ) latest
        """
    )
    spark.sql(
        f"""
        MERGE INTO {repositories} target
        USING github_repositories source
        ON target.repository_id = source.repository_id
        WHEN MATCHED AND struct(
            source.last_observed_at, source.last_event_id
        ) > struct(
            target.last_observed_at, target.last_event_id
        ) THEN UPDATE SET
            repository_name = source.repository_name,
            repository_owner = source.repository_owner,
            last_observed_at = source.last_observed_at,
            last_event_id = source.last_event_id,
            curated_at = source.curated_at
        WHEN NOT MATCHED THEN INSERT (
            repository_id,
            repository_name,
            repository_owner,
            last_observed_at,
            last_event_id,
            curated_at
        ) VALUES (
            source.repository_id,
            source.repository_name,
            source.repository_owner,
            source.last_observed_at,
            source.last_event_id,
            source.curated_at
        )
        """
    )
    spark.catalog.uncacheTable("github_day")
