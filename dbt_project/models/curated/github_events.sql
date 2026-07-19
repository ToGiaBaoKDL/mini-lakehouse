-- github_events.sql
-- Bảng sự kiện curated sạch sẽ, loại bỏ trùng lặp và trích xuất các thuộc tính payload phổ biến (ClickHouse)

WITH deduplicated AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY id ORDER BY created_at DESC, ingested_at DESC) AS row_num
    FROM {{ ref('stg_github_events') }}
)

SELECT
    id,
    type,
    actor_id,
    actor_login,
    repo_id,
    repo_name,
    public,
    created_at,
    -- Trích xuất trường từ payload JSON sử dụng hàm JSONExtract của ClickHouse
    JSONExtractString(payload, 'action') AS action,
    JSONExtractString(payload, 'ref_type') AS ref_type,
    JSONExtractInt(payload, 'issue', 'number') AS issue_number,
    JSONExtractInt(payload, 'pull_request', 'number') AS pull_request_number,
    JSONExtract(payload, 'comment', 'id', 'Int64') AS comment_id,
    source_file,
    source_hour,
    ingested_at
FROM deduplicated
WHERE row_num = 1
