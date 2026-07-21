{{
    config(
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
    repository_id,
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
    ingested_at
from {{ ref('int_github__events_enriched') }}
{% if is_incremental() %}
where source_hour >= (
    select coalesce(max(source_hour), timestamp '1970-01-01 00:00:00 UTC')
    from {{ this }}
) - interval '2' hour
{% endif %}
