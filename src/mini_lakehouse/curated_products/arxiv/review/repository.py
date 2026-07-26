"""Bounded read queries for the ArXiv OCR review surface."""

from __future__ import annotations

from typing import Any

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import PlatformContracts, load_contracts
from mini_lakehouse.curated_products.arxiv.review.models import (
    OcrDocumentFilter,
    OcrDocumentRun,
    OcrDocumentSummary,
    OcrPageElement,
)
from mini_lakehouse.platform.trino import QueryResult, SqlExecutor


def _records(result: QueryResult) -> tuple[dict[str, Any], ...]:
    return tuple(dict(zip(result.columns, row, strict=True)) for row in result.rows)


class ArxivOcrReviewRepository:
    """Read current OCR state without sharing mutable ingestion repository logic."""

    def __init__(
        self,
        settings: Settings,
        contracts: PlatformContracts | None = None,
    ) -> None:
        self._settings = settings
        registry = contracts or load_contracts(settings.contracts_dir)
        self._product = registry.curated_product("arxiv")

    def _relation(self, key: str) -> str:
        return self._product.table_identifier(key).trino(self._settings.trino.catalog)

    def documents(
        self,
        executor: SqlExecutor,
        filters: OcrDocumentFilter,
    ) -> tuple[OcrDocumentSummary, ...]:
        search = filters.search.strip().lower()
        result = executor.execute(
            f"""
            WITH ranked_runs AS (
                SELECT
                    document.*,
                    row_number() OVER (
                        PARTITION BY document.arxiv_id
                        ORDER BY
                            document.prepared_at DESC,
                            document.attempt_count DESC,
                            document.batch_id DESC
                    ) AS run_rank
                FROM {self._relation("ocr_document_runs")} AS document
            )
            SELECT
                run.arxiv_id,
                paper.title,
                run.state,
                run.attempt_count,
                run.page_count,
                run.processing_id,
                run.model_repository,
                run.model_revision,
                run.completed_at,
                run.error_code
            FROM ranked_runs AS run
            LEFT JOIN {self._relation("papers")} AS paper
              ON paper.arxiv_id = run.arxiv_id
            WHERE run.run_rank = 1
              AND (? = 'all' OR run.state = ?)
              AND (
                    ? = ''
                    OR strpos(lower(run.arxiv_id), ?) > 0
                    OR strpos(lower(coalesce(paper.title, '')), ?) > 0
              )
            ORDER BY coalesce(run.completed_at, run.prepared_at) DESC, run.arxiv_id
            LIMIT {filters.limit}
            """,
            (filters.state, filters.state, search, search, search),
        )
        return tuple(OcrDocumentSummary.model_validate(record) for record in _records(result))

    def document_runs(
        self,
        executor: SqlExecutor,
        arxiv_id: str,
    ) -> tuple[OcrDocumentRun, ...]:
        result = executor.execute(
            f"""
            SELECT
                document.request_id,
                document.batch_id,
                document.arxiv_id,
                paper.title,
                paper.abstract,
                document.pdf_url,
                document.oai_datestamp,
                document.state,
                document.attempt_count,
                document.processing_id,
                document.artifact_uri,
                document.manifest_sha256,
                document.pdf_sha256,
                document.pdf_size_bytes,
                document.page_count,
                document.model_repository,
                document.model_revision,
                document.layout_model_repository,
                document.layout_model_revision,
                document.adapter_version,
                document.error_code,
                document.error_message,
                document.prepared_at,
                document.completed_at
            FROM {self._relation("ocr_document_runs")} AS document
            LEFT JOIN {self._relation("papers")} AS paper
              ON paper.arxiv_id = document.arxiv_id
            WHERE document.arxiv_id = ?
            ORDER BY
                document.prepared_at DESC,
                document.attempt_count DESC,
                document.batch_id DESC
            LIMIT 100
            """,
            (arxiv_id,),
        )
        return tuple(OcrDocumentRun.model_validate(record) for record in _records(result))

    def page_elements(
        self,
        executor: SqlExecutor,
        *,
        processing_id: str,
        page_number: int,
    ) -> tuple[OcrPageElement, ...]:
        if page_number < 1:
            raise ValueError("page_number must be positive")
        result = executor.execute(
            f"""
            SELECT
                element_id,
                page_number,
                reading_order,
                element_type,
                bbox_json,
                text_content,
                markdown_content,
                parent_element_id,
                raw_attributes_json
            FROM {self._relation("ocr_document_elements")}
            WHERE processing_id = ?
              AND page_number = ?
            ORDER BY reading_order, element_id
            """,
            (processing_id, page_number),
        )
        return tuple(OcrPageElement.model_validate(record) for record in _records(result))
