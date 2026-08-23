{{
    config(
        partitioned_by=['month(started_at)']
    )
}}

select
    runs.run_id,
    runs.request_id,
    runs.arxiv_id,
    runs.oai_datestamp,
    runs.source_record_sha256,
    runs.pdf_sha256,
    runs.pdf_size_bytes,
    runs.page_count,
    runs.processing_id,
    runs.processor,
    runs.model_repository,
    runs.model_revision,
    runs.layout_model_repository,
    runs.layout_model_revision,
    runs.adapter_version,
    runs.config_hash,
    runs.state,
    runs.attempt,
    runs.provider,
    runs.provider_reference,
    runs.provider_run_id,
    runs.started_at,
    runs.completed_at,
    case
        when runs.completed_at >= runs.started_at
            then date_diff('second', runs.started_at, runs.completed_at)
    end as duration_seconds,
    runs.completed_at is not null as is_terminal,
    runs.processing_id is not null
        and runs.artifact_uri is not null
        and runs.manifest_sha256 is not null as has_published_artifact,
    runs.artifact_uri,
    runs.manifest_sha256,
    profile.observed_page_count,
    profile.element_count,
    profile.element_type_count,
    profile.text_character_count,
    profile.markdown_element_count,
    runs.curated_at
from {{ ref('stg_arxiv__ocr_document_runs') }} as runs
left join {{ ref('int_research__ocr_content_profile') }} as profile
    on runs.processing_id = profile.processing_id
    and runs.arxiv_id = profile.arxiv_id
