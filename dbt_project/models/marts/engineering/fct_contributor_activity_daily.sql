{{
    config(
        incremental_strategy='merge',
        on_schema_change='sync_all_columns',
        unique_key=['activity_date', 'actor_id'],
        properties={
            'format': "'PARQUET'",
            'format_version': '2',
            'partitioning': "ARRAY['month(activity_date)']"
        }
    )
}}

with events as (
    select *
    from {{ ref('fct_github_events') }}
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
        count_if(event_type = 'PushEvent') as push_event_count,
        sum(coalesce(push_commit_count, 0)) as pushed_commit_count,
        count_if(event_type = 'PullRequestEvent') as pull_request_event_count,
        count_if(event_type = 'IssuesEvent') as issue_event_count,
        count_if(event_type = 'IssueCommentEvent') as issue_comment_event_count
    from events
    group by event_date_utc, actor_id
)

select
    aggregated.*,
    actors.actor_login,
    actors.is_bot
from aggregated
left join {{ ref('dim_github_actors') }} as actors using (actor_id)
