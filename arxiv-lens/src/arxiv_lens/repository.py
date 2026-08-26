"""Bounded Athena queries for ArXiv Lens."""

from collections.abc import Callable, Mapping
from typing import Any

import awswrangler as wr
import boto3
import pandas as pd
from lakehouse.aws import get_runtime_parameter
from lakehouse.config.settings import Settings

from arxiv_lens.models import OcrDocument, OcrDocumentFilter, OcrDocumentSummary
from lakehouse.contracts import DataContracts, load_contracts

type Query = Callable[..., pd.DataFrame]


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    normalized = frame.astype(object).where(frame.notna(), None)
    return normalized.to_dict(orient="records")


class ArxivRepository:
    def __init__(
        self,
        settings: Settings,
        contracts: DataContracts | None = None,
        query: Query | None = None,
    ) -> None:
        registry = contracts or load_contracts(settings.contracts_dir)
        self._product = registry.curated_product("arxiv")
        self._database = self._product.database
        self._query_override = query
        if query is None:
            self._query_output = get_runtime_parameter(
                settings.environment,
                "athena/arxiv_lens_output_uri",
            )
            self._session = boto3.Session(region_name=settings.aws_region)
        else:
            self._query_output = ""
            self._session = None

    def _query(
        self,
        sql: str,
        *,
        parameters: Mapping[str, Any] | None = None,
    ) -> pd.DataFrame:
        if self._query_override is not None:
            return self._query_override(
                sql,
                database=self._database,
                parameters=parameters,
            )
        return wr.athena.read_sql_query(
            sql=sql,
            database=self._database,
            params=dict(parameters or {}),
            paramstyle="named",
            ctas_approach=False,
            unload_approach=False,
            dtype_backend="pyarrow",
            workgroup="primary",
            s3_output=self._query_output,
            boto3_session=self._session,
        )

    def _relation(self, key: str) -> str:
        table = self._product.table(key)
        return f'"{self._database}"."{table.name}"'

    def documents(
        self,
        filters: OcrDocumentFilter,
    ) -> tuple[OcrDocumentSummary, ...]:
        search = filters.search
        frame = self._query(
            f"""
            SELECT
                document.arxiv_id,
                paper.title,
                document.page_count,
                document.processing_id,
                document.model_repository,
                document.model_revision,
                document.processed_at
            FROM {self._relation("ocr_documents")} AS document
            LEFT JOIN {self._relation("papers")} AS paper
              ON paper.arxiv_id = document.arxiv_id
            WHERE (
                    :search = ''
                    OR strpos(lower(document.arxiv_id), :search) > 0
                    OR strpos(lower(coalesce(paper.title, '')), :search) > 0
              )
            ORDER BY document.processed_at DESC, document.arxiv_id
            LIMIT {filters.limit}
            """,
            parameters={"search": search},
        )
        return tuple(OcrDocumentSummary.model_validate(record) for record in _records(frame))

    def document(self, arxiv_id: str) -> OcrDocument | None:
        frame = self._query(
            f"""
            SELECT
                document.arxiv_id,
                paper.title,
                paper.abstract,
                document.pdf_url,
                document.oai_datestamp,
                document.source_record_sha256,
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
                document.sdk_version,
                document.config_hash,
                document.processed_at
            FROM {self._relation("ocr_documents")} AS document
            LEFT JOIN {self._relation("papers")} AS paper
              ON paper.arxiv_id = document.arxiv_id
            WHERE document.arxiv_id = :arxiv_id
            LIMIT 1
            """,
            parameters={"arxiv_id": arxiv_id},
        )
        records = _records(frame)
        return OcrDocument.model_validate(records[0]) if records else None
