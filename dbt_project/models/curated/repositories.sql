-- repositories.sql
-- Bảng chiều (dimension table) quản lý thông tin các repositories, lấy tên mới nhất của repo

WITH ranked_repos AS (
    SELECT
        repo_id,
        repo_name,
        created_at,
        ROW_NUMBER() OVER (PARTITION BY repo_id ORDER BY created_at DESC) AS rn
    FROM {{ ref('stg_github_events') }}
    WHERE repo_id IS NOT NULL
)

SELECT
    repo_id,
    repo_name,
    created_at AS last_updated_at
FROM ranked_repos
WHERE rn = 1
