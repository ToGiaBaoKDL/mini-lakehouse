select
    arxiv_id,
    oai_identifier,
    title,
    abstract,
    license_uri,
    doi,
    journal_ref,
    comments,
    created_date,
    updated_date,
    oai_datestamp,
    pdf_url,
    is_deleted,
    source_record_sha256,
    curated_at
from {{ source('arxiv', 'papers') }}
