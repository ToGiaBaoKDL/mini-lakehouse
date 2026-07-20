{{
    config(
        unique_key=['activity_date', 'repository_id'],
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
        count_if(event_type = 'PushEvent') as push_event_count,
        sum(coalesce(push_commit_count, 0)) as pushed_commit_count,
        count_if(event_type = 'PullRequestEvent') as pull_request_event_count,
        count(distinct pull_request_number) as unique_pull_request_count,
        count_if(event_type = 'IssuesEvent') as issue_event_count,
        count_if(event_type = 'IssueCommentEvent') as issue_comment_event_count,
        count(distinct actor_id) as active_actor_count
    from events
    group by event_date_utc, repository_id
)

select
    aggregated.*,
    repositories.repository_name,
    repositories.repository_owner
from aggregated
left join {{ ref('dim_github_repositories') }} as repositories using (repository_id)
