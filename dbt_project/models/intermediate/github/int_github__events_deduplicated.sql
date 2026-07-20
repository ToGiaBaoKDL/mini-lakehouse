with ranked as (
    select
        *,
        row_number() over (
            partition by event_id
            order by ingested_at desc, source_hour desc, source_file desc
        ) as ingestion_rank
    from {{ ref('stg_github_archive__events') }}
)

select *
from ranked
where ingestion_rank = 1
