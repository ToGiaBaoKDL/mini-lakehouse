select
    actor_id,
    actor_login,
    ends_with(lower(coalesce(actor_login, '')), '[bot]')
        or ends_with(lower(coalesce(actor_login, '')), '-bot') as is_bot,
    last_observed_at
from {{ ref('int_github__actors_latest') }}
