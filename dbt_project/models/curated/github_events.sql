-- github_events.sql
-- Bảng sự kiện curated sạch sẽ, loại bỏ trùng lặp và trích xuất các thuộc tính payload phổ biến (Trino)

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
    -- Trích xuất trường từ payload JSON sử dụng hàm json_extract_scalar của Trino
    json_extract_scalar(payload, '$.action') AS action,
    json_extract_scalar(payload, '$.ref_type') AS ref_type,
    cast(json_extract_scalar(payload, '$.issue.number') as bigint) AS issue_number,
    cast(json_extract_scalar(payload, '$.pull_request.number') as bigint) AS pull_request_number,
    cast(json_extract_scalar(payload, '$.comment.id') as bigint) AS comment_id,
    source_file,
    source_hour,
    ingested_at
FROM deduplicated
WHERE row_num = 1
