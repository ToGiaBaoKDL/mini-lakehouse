select
    *,
    event_type = 'PushEvent' as is_push_event,
    event_type = 'PullRequestEvent' as is_pull_request_event,
    event_type = 'IssuesEvent' as is_issue_event,
    event_type = 'IssueCommentEvent' as is_issue_comment_event
from {{ ref('stg_github__events') }}
