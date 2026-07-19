-- stg_github_events.sql
-- Phẳng hóa cơ bản dữ liệu sự kiện từ Landing layer (Đọc trực tiếp qua ClickHouse file table function với cấu trúc cố định)

WITH source_data AS (
    SELECT * FROM file(
        'warehouse/landing_api_github/events_raw/data/**/*.parquet', 
        'Parquet', 
        'id Nullable(String), type Nullable(String), actor_id Nullable(Int64), actor_login Nullable(String), repo_id Nullable(Int64), repo_name Nullable(String), payload Nullable(String), public Nullable(Bool), created_at Nullable(String), source_file Nullable(String), source_hour Nullable(String), ingested_at Nullable(String)'
    )
)

SELECT
    id,
    type,
    actor_id,
    actor_login,
    repo_id,
    repo_name,
    payload,
    public,
    parseDateTime64BestEffort(created_at) AS created_at,
    source_file,
    source_hour,
    parseDateTime64BestEffort(ingested_at) AS ingested_at
FROM source_data
