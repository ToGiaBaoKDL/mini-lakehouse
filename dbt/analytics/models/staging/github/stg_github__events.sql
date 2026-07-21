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
    push_commit_count,
    event_action,
    ref_type,
    issue_number,
    pull_request_number,
    comment_id,
    source_hour
from {{ source('github', 'events') }}
