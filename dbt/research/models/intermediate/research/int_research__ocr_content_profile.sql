select
    processing_id,
    arxiv_id,
    count(distinct page_number) as observed_page_count,
    count(*) as element_count,
    count(distinct element_type) as element_type_count,
    sum(length(text_content)) as text_character_count,
    count_if(markdown_content is not null and trim(markdown_content) <> '')
        as markdown_element_count
from {{ ref('stg_arxiv__ocr_document_elements') }}
group by processing_id, arxiv_id
