from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CuratedTableSpec:
    schema_contract: str
    primary_key: tuple[str, ...]
    columns: tuple[tuple[str, str, bool], ...]
    partitioning: tuple[str, ...] = ()


EVENTS_SPEC = CuratedTableSpec(
    schema_contract="github.events.v1",
    primary_key=("event_id",),
    columns=(
        ("event_id", "varchar", True),
        ("event_type", "varchar", True),
        ("actor_id", "bigint", False),
        ("actor_login", "varchar", False),
        ("repository_id", "bigint", False),
        ("repository_name", "varchar", False),
        ("is_public", "boolean", True),
        ("occurred_at", "timestamp(6) with time zone", True),
        ("event_date_utc", "date", True),
        ("push_commit_count", "bigint", False),
        ("event_action", "varchar", False),
        ("ref_type", "varchar", False),
        ("issue_number", "bigint", False),
        ("pull_request_number", "bigint", False),
        ("comment_id", "bigint", False),
        ("source_file", "varchar", True),
        ("source_hour", "timestamp(6) with time zone", True),
        ("ingested_at", "timestamp(6) with time zone", True),
        ("curated_at", "timestamp(6) with time zone", True),
    ),
    partitioning=("day(event_date_utc)",),
)

ACTORS_CURRENT_SPEC = CuratedTableSpec(
    schema_contract="github.actors_current.v1",
    primary_key=("actor_id",),
    columns=(
        ("actor_id", "bigint", True),
        ("actor_login", "varchar", False),
        ("is_bot", "boolean", True),
        ("last_observed_at", "timestamp(6) with time zone", True),
        ("curated_at", "timestamp(6) with time zone", True),
    ),
)

REPOSITORIES_CURRENT_SPEC = CuratedTableSpec(
    schema_contract="github.repositories_current.v1",
    primary_key=("repository_id",),
    columns=(
        ("repository_id", "bigint", True),
        ("repository_name", "varchar", False),
        ("repository_owner", "varchar", False),
        ("last_observed_at", "timestamp(6) with time zone", True),
        ("curated_at", "timestamp(6) with time zone", True),
    ),
)

TABLE_SPECS = {
    "events": EVENTS_SPEC,
    "actors_current": ACTORS_CURRENT_SPEC,
    "repositories_current": REPOSITORIES_CURRENT_SPEC,
}
