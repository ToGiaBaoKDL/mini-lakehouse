"""Bounded Athena queries for the ArXiv document inspection surface."""

from collections.abc import Mapping
from typing import Any, Protocol

import pandas as pd
from lakehouse.aws import get_runtime_parameter
from lakehouse.config.settings import Settings
from lakehouse.contracts import DataContracts, load_contracts

from apps.arxiv_inspector.data.athena import AthenaReader
from apps.arxiv_inspector.data.models import (
    OcrDocumentFilter,
    OcrDocumentRun,
    OcrDocumentSummary,
)


class QueryReader(Protocol):
    def query(
        self,
        sql: str,
        *,
        database: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> pd.DataFrame: ...


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    normalized = frame.astype(object).where(frame.notna(), None)
    return normalized.to_dict(orient="records")


class ArxivDocumentRepository:
    def __init__(
        self,
        settings: Settings,
        contracts: DataContracts | None = None,
        reader: QueryReader | None = None,
    ) -> None:
        registry = contracts or load_contracts(settings.contracts_dir)
        self._product = registry.curated_product("arxiv")
        self._database = self._product.database
        self._reader = reader or AthenaReader(
            workgroup="primary",
            s3_output=get_runtime_parameter(
                settings.environment,
                "athena/arxiv_inspector_output_uri",
            ),
            region_name=settings.aws_region,
        )

    def _relation(self, key: str) -> str:
        table = self._product.table(key)
        return f'"{self._database}"."{table.name}"'

    def documents(
        self,
        filters: OcrDocumentFilter,
    ) -> tuple[OcrDocumentSummary, ...]:
        search = filters.search
        frame = self._reader.query(
            f"""
            WITH ranked_runs AS (
                SELECT
                    document.arxiv_id,
                    document.state,
                    document.attempt,
                    document.page_count,
                    document.processing_id,
                    coalesce(document.processor, 'glm_ocr') AS processor,
                    document.model_repository,
                    document.model_revision,
                    document.completed_at,
                    document.started_at,
                    document.run_id,
                    row_number() OVER (
                        PARTITION BY document.arxiv_id
                        ORDER BY
                            document.started_at DESC,
                            document.attempt DESC,
                            document.run_id DESC
                    ) AS run_rank
                FROM {self._relation("ocr_document_runs")} AS document
            )
            SELECT
                run.arxiv_id,
                paper.title,
                run.state,
                run.attempt,
                run.page_count,
                run.processing_id,
                run.processor,
                run.model_repository,
                run.model_revision,
                run.completed_at
            FROM ranked_runs AS run
            LEFT JOIN {self._relation("papers")} AS paper
              ON paper.arxiv_id = run.arxiv_id
            WHERE run.run_rank = 1
              AND (:state = 'all' OR run.state = :state)
              AND (
                    :search = ''
                    OR strpos(lower(run.arxiv_id), :search) > 0
                    OR strpos(lower(coalesce(paper.title, '')), :search) > 0
              )
            ORDER BY coalesce(run.completed_at, run.started_at) DESC, run.arxiv_id
            LIMIT {filters.limit}
            """,
            database=self._database,
            parameters={"state": filters.state.value, "search": search},
        )
        return tuple(OcrDocumentSummary.model_validate(record) for record in _records(frame))

    def document_run(self, arxiv_id: str) -> OcrDocumentRun | None:
        frame = self._reader.query(
            f"""
            SELECT
                document.request_id,
                document.run_id,
                document.arxiv_id,
                paper.title,
                paper.abstract,
                document.pdf_url,
                document.oai_datestamp,
                document.state,
                document.attempt,
                document.processing_id,
                document.artifact_uri,
                document.manifest_sha256,
                document.pdf_sha256,
                document.pdf_size_bytes,
                document.page_count,
                coalesce(document.processor, 'glm_ocr') AS processor,
                document.model_repository,
                document.model_revision,
                document.layout_model_repository,
                document.layout_model_revision,
                document.adapter_version,
                document.started_at,
                document.completed_at
            FROM {self._relation("ocr_document_runs")} AS document
            LEFT JOIN {self._relation("papers")} AS paper
              ON paper.arxiv_id = document.arxiv_id
            WHERE document.arxiv_id = :arxiv_id
            ORDER BY
                document.started_at DESC,
                document.attempt DESC,
                document.run_id DESC
            LIMIT 1
            """,
            database=self._database,
            parameters={"arxiv_id": arxiv_id},
        )
        records = _records(frame)
        return OcrDocumentRun.model_validate(records[0]) if records else None
