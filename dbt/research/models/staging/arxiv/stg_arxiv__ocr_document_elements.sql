select
    processing_id,
    element_id,
    arxiv_id,
    page_number,
    reading_order,
    element_type,
    bbox_json,
    text_content,
    markdown_content,
    parent_element_id,
    raw_attributes_json,
    curated_at
from {{ source('arxiv', 'ocr_document_elements') }}
