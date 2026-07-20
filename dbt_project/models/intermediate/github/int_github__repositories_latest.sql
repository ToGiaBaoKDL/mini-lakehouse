select
    repository_id,
    max_by(repository_name, occurred_at) filter (
        where repository_name is not null
    ) as repository_name,
    max(occurred_at) as last_observed_at
from {{ ref('int_github__events_enriched') }}
where repository_id is not null
group by repository_id
