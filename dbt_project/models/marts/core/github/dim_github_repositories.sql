select
    repository_id,
    repository_name,
    split_part(repository_name, '/', 1) as repository_owner,
    last_observed_at
from {{ ref('int_github__repositories_latest') }}
