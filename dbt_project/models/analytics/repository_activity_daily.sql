-- repository_activity_daily.sql
-- Bảng tổng hợp hoạt động hàng ngày của từng repository, tối ưu cho dashboard BI (Trino)

SELECT
    cast(created_at as date) AS activity_date,
    repo_id,
    repo_name,
    COUNT(*) AS total_events,
    COUNT(CASE WHEN type = 'PushEvent' THEN 1 END) AS push_count,
    COUNT(CASE WHEN type = 'PullRequestEvent' THEN 1 END) AS pull_request_count,
    COUNT(CASE WHEN type = 'IssuesEvent' THEN 1 END) AS issue_count,
    COUNT(CASE WHEN type = 'IssueCommentEvent' THEN 1 END) AS issue_comment_count,
    COUNT(DISTINCT actor_id) AS active_contributors
FROM {{ ref('github_events') }}
WHERE repo_id IS NOT NULL
GROUP BY cast(created_at as date), repo_id, repo_name
