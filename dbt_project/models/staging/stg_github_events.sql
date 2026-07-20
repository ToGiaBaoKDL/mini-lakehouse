-- stg_github_events.sql
-- Phẳng hóa cơ bản dữ liệu sự kiện từ Landing layer (Truy vấn qua Iceberg REST Catalog từ Trino)

WITH source_data AS (
    SELECT * FROM iceberg."landing.api.github".events_raw
)

SELECT
    id,
    type,
    cast(actor_id as bigint) AS actor_id,
    actor_login,
    cast(repo_id as bigint) AS repo_id,
    repo_name,
    payload,
    public,
    cast(from_iso8601_timestamp(created_at) as timestamp(6) with time zone) AS created_at,
    source_file,
    source_hour,
    cast(from_iso8601_timestamp(ingested_at) as timestamp(6) with time zone) AS ingested_at
FROM source_data
