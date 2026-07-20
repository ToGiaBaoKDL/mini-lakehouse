select
    event_id,
    event_type,
    actor_id,
    nullif(trim(actor_login), '') as actor_login,
    repository_id,
    nullif(trim(repository_name), '') as repository_name,
    payload_json,
    is_public,
    occurred_at,
    cast(occurred_at at time zone 'UTC' as date) as event_date_utc,
    source_file,
    source_hour,
    ingested_at
from {{ source('github_archive', 'events_raw') }}
