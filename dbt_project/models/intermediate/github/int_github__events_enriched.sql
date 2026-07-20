{{
    config(
        materialized='table',
        schema='curated.github.internal',
        properties={
            'format': "'PARQUET'",
            'format_version': '2',
            'partitioning': "ARRAY['month(event_date_utc)']"
        }
    )
}}

select
    event_id,
    event_type,
    actor_id,
    actor_login,
    repository_id,
    repository_name,
    is_public,
    occurred_at,
    event_date_utc,
    try_cast(json_extract_scalar(payload_json, '$.size') as bigint) as push_commit_count,
    json_extract_scalar(payload_json, '$.action') as event_action,
    json_extract_scalar(payload_json, '$.ref_type') as ref_type,
    try_cast(json_extract_scalar(payload_json, '$.issue.number') as bigint) as issue_number,
    try_cast(
        json_extract_scalar(payload_json, '$.pull_request.number') as bigint
    ) as pull_request_number,
    try_cast(json_extract_scalar(payload_json, '$.comment.id') as bigint) as comment_id,
    source_file,
    source_hour,
    ingested_at
from {{ ref('int_github__events_deduplicated') }}
