{{
    config(
        partitioned_by=['month(activity_date)']
    )
}}

with events as (
    select
        event_date_utc,
        repository_id,
        actor_id,
        push_commit_count,
        pull_request_number,
        is_push_event,
        is_pull_request_event,
        is_issue_event,
        is_issue_comment_event
    from {{ ref('int_engineering__events_classified') }}
    where repository_id is not null
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
        repository_id,
        count(*) as event_count,
        count_if(is_push_event) as push_event_count,
        sum(coalesce(push_commit_count, 0)) as pushed_commit_count,
        count_if(is_pull_request_event) as pull_request_event_count,
        count(distinct pull_request_number) as unique_pull_request_count,
        count_if(is_issue_event) as issue_event_count,
        count_if(is_issue_comment_event) as issue_comment_event_count,
        count(distinct actor_id) as active_actor_count
    from events
    group by event_date_utc, repository_id
)

select
    aggregated.activity_date,
    aggregated.repository_id,
    aggregated.event_count,
    aggregated.push_event_count,
    aggregated.pushed_commit_count,
    aggregated.pull_request_event_count,
    aggregated.unique_pull_request_count,
    aggregated.issue_event_count,
    aggregated.issue_comment_event_count,
    aggregated.active_actor_count,
    repositories.repository_name,
    repositories.repository_owner
from aggregated
left join {{ ref('stg_github__repositories_current') }} as repositories
    on aggregated.repository_id = repositories.repository_id
