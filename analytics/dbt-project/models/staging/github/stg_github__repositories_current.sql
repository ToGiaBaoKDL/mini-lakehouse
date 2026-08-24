select
    repository_id,
    repository_name,
    repository_owner,
    last_observed_at
from {{ source('github', 'repositories_current') }}
