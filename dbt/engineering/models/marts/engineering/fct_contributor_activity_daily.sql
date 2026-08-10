{{
    config(
        partitioned_by=['month(activity_date)']
    )
}}

with events as (
    select
        event_date_utc,
        actor_id,
        repository_id,
        push_commit_count,
        is_push_event,
        is_pull_request_event,
        is_issue_event,
        is_issue_comment_event
    from {{ ref('int_engineering__events_classified') }}
    where actor_id is not null
    {% if is_incremental() %}
      and event_date_utc >= (
          select coalesce(max(activity_date), date '1970-01-01')
          from {{ this }}
      ) - interval '2' day
    {% endif %}
),

aggregated as (
    select
        event_date_utc as activity_date,
        actor_id,
        count(*) as event_count,
        count(distinct repository_id) as active_repository_count,
        count_if(is_push_event) as push_event_count,
        sum(coalesce(push_commit_count, 0)) as pushed_commit_count,
        count_if(is_pull_request_event) as pull_request_event_count,
        count_if(is_issue_event) as issue_event_count,
        count_if(is_issue_comment_event) as issue_comment_event_count
    from events
    group by event_date_utc, actor_id
)

select
    aggregated.activity_date,
    aggregated.actor_id,
    aggregated.event_count,
    aggregated.active_repository_count,
    aggregated.push_event_count,
    aggregated.pushed_commit_count,
    aggregated.pull_request_event_count,
    aggregated.issue_event_count,
    aggregated.issue_comment_event_count,
    actors.actor_login,
    actors.is_bot
from aggregated
left join {{ ref('stg_github__actors_current') }} as actors
    on aggregated.actor_id = actors.actor_id
