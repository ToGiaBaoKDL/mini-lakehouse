select
    actor_id,
    max_by(actor_login, occurred_at) filter (where actor_login is not null) as actor_login,
    max(occurred_at) as last_observed_at
from {{ ref('int_github__events_enriched') }}
where actor_id is not null
group by actor_id
