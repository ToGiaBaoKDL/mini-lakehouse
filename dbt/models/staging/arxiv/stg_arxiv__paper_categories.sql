select
    arxiv_id,
    category,
    is_primary,
    oai_datestamp,
    curated_at
from {{ source('arxiv', 'paper_categories') }}
