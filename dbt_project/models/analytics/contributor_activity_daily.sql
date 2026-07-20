-- contributor_activity_daily.sql
-- Bảng tổng hợp hoạt động đóng góp hàng ngày của các contributors (Trino)

SELECT
    cast(created_at as date) AS activity_date,
    actor_id,
    actor_login,
    COUNT(*) AS total_events,
    COUNT(CASE WHEN type = 'PushEvent' THEN 1 END) AS push_count,
    COUNT(CASE WHEN type = 'PullRequestEvent' THEN 1 END) AS pull_request_count,
    COUNT(DISTINCT repo_id) AS distinct_repos_contributed
FROM {{ ref('github_events') }}
GROUP BY cast(created_at as date), actor_id, actor_login
