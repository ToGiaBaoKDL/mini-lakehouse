{{
    config(
        partitioned_by=['month(publication_date)']
    )
}}

select
    created_date as publication_date,
    coalesce(primary_category, 'uncategorized') as primary_category,
    count(*) as paper_count,
    count_if(updated_date > created_date) as revised_paper_count,
    count_if(is_cross_listed) as cross_listed_paper_count,
    count_if(has_doi) as papers_with_doi_count,
    count_if(has_journal_reference) as papers_with_journal_reference_count,
    sum(author_count) as author_occurrence_count,
    avg(author_count) as avg_author_count,
    avg(category_count) as avg_category_count,
    avg(revision_lag_days) as avg_revision_lag_days
from {{ ref('int_research__papers_enriched') }}
where created_date is not null
group by created_date, coalesce(primary_category, 'uncategorized')
