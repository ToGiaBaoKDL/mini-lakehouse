select
    actor_id,
    actor_login,
    is_bot,
    last_observed_at
from {{ source('github', 'actors_current') }}
