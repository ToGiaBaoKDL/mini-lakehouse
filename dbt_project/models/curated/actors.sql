-- actors.sql
-- Bảng chiều (dimension table) quản lý thông tin các actors/users hoạt động trên GitHub

WITH ranked_actors AS (
    SELECT
        actor_id,
        actor_login,
        created_at,
        ROW_NUMBER() OVER (PARTITION BY actor_id ORDER BY created_at DESC) AS rn
    FROM {{ ref('stg_github_events') }}
    WHERE actor_id IS NOT NULL
)

SELECT
    actor_id,
    actor_login,
    created_at AS last_seen_at
FROM ranked_actors
WHERE rn = 1
