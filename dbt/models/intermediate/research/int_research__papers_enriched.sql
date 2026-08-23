with authors as (
    select
        arxiv_id,
        count(*) as author_count
    from {{ ref('stg_arxiv__paper_authors') }}
    group by arxiv_id
),

categories as (
    select
        arxiv_id,
        count(*) as category_count,
        max(case when is_primary then category end) as primary_category
    from {{ ref('stg_arxiv__paper_categories') }}
    group by arxiv_id
)

select
    papers.arxiv_id,
    papers.oai_identifier,
    papers.title,
    papers.abstract,
    papers.license_uri,
    papers.doi,
    papers.journal_ref,
    papers.comments,
    papers.created_date,
    papers.updated_date,
    papers.oai_datestamp,
    papers.pdf_url,
    coalesce(authors.author_count, 0) as author_count,
    coalesce(categories.category_count, 0) as category_count,
    categories.primary_category,
    papers.abstract is not null and trim(papers.abstract) <> '' as has_abstract,
    papers.license_uri is not null and trim(papers.license_uri) <> '' as has_license,
    papers.doi is not null and trim(papers.doi) <> '' as has_doi,
    papers.journal_ref is not null and trim(papers.journal_ref) <> '' as has_journal_reference,
    coalesce(categories.category_count, 0) > 1 as is_cross_listed,
    case
        when papers.created_date is not null
            and papers.updated_date >= papers.created_date
            then date_diff('day', papers.created_date, papers.updated_date)
    end as revision_lag_days,
    papers.curated_at
from {{ ref('stg_arxiv__papers') }} as papers
left join authors on papers.arxiv_id = authors.arxiv_id
left join categories on papers.arxiv_id = categories.arxiv_id
where not papers.is_deleted
