select
    arxiv_id,
    author_position,
    keyname,
    forenames,
    suffix,
    affiliation,
    oai_datestamp,
    curated_at
from {{ source('arxiv', 'paper_authors') }}
