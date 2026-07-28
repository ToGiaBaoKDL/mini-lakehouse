"""Bounded SQL mutations for ArXiv OCR state and canonical elements."""

from collections.abc import Sequence
from typing import Any, Literal

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts import PlatformContracts, load_contracts
from mini_lakehouse.contracts.processors import ProcessorContract
from mini_lakehouse.curated.arxiv.models import (
    ActiveOcrBatch,
    OcrBatchDocument,
    OcrCandidate,
    OcrRunState,
)
from mini_lakehouse.platform.trino import SqlExecutor
from mini_lakehouse.processing.ocr.core.protocol import (
    OcrDocumentResult,
    OcrElement,
    OcrJob,
    OcrReuseReference,
)


class ArxivOcrRepository:
    def __init__(
        self,
        settings: Settings,
        contracts: PlatformContracts | None = None,
    ) -> None:
        self._settings = settings
        registry = contracts or load_contracts(settings.contracts_dir)
        self._product = registry.curated_product("arxiv")

    def _relation(self, key: str) -> str:
        return self._product.table_identifier(key).trino(self._settings.polaris.catalog_name)

    def active_batch(self, executor: SqlExecutor) -> ActiveOcrBatch | None:
        result = executor.execute(
            f"""
            SELECT
                batch.batch_id,
                batch.state,
                batch.provider_run_id,
                batch.provider,
                batch.provider_reference,
                batch.request_count,
                batch.job_json,
                document.request_id,
                document.arxiv_id,
                document.oai_datestamp,
                document.source_record_sha256,
                document.pdf_url,
                document.attempt_count,
                document.state
            FROM {self._relation("ocr_batches")} AS batch
            LEFT JOIN {self._relation("ocr_document_runs")} AS document
              ON document.batch_id = batch.batch_id
            WHERE batch.state IN ('prepared', 'submitted', 'running')
            ORDER BY batch.prepared_at, batch.batch_id, document.arxiv_id
            """
        )
        batch_ids = {str(row[0]) for row in result.rows}
        if len(batch_ids) > 1:
            raise RuntimeError(f"Multiple active ArXiv OCR batches found: {sorted(batch_ids)!r}")
        if not result.rows:
            return None
        first = result.rows[0]
        if first[3] is None or first[4] is None:
            raise RuntimeError(
                f"Active ArXiv OCR batch {first[0]} has no execution provider identity"
            )
        request_count = int(first[5])
        present_documents = sum(row[7] is not None for row in result.rows)
        if request_count != present_documents:
            raise RuntimeError(
                f"ArXiv OCR batch {first[0]} expects {request_count} documents, "
                f"found {present_documents}"
            )
        raw_job = first[6]
        if raw_job is None:
            raise RuntimeError(f"Active ArXiv OCR batch {first[0]} has no immutable job payload")
        try:
            job = OcrJob.model_validate_json(str(raw_job))
        except ValueError as error:
            raise RuntimeError(
                f"Active ArXiv OCR batch {first[0]} has an invalid immutable job payload"
            ) from error
        document_rows = {str(row[7]): row for row in result.rows if row[7] is not None}
        requests = {request.request_id: request for request in job.documents}
        if document_rows.keys() != requests.keys():
            raise RuntimeError(
                f"ArXiv OCR batch {first[0]} persisted documents differ from its immutable job"
            )
        for request_id, request in requests.items():
            row = document_rows[request_id]
            persisted_request = (
                str(row[8]),
                row[9],
                str(row[10]),
                str(row[11]),
            )
            job_request = (
                request.arxiv_id,
                request.oai_datestamp,
                request.source_record_sha256,
                request.pdf_url,
            )
            if persisted_request != job_request:
                raise RuntimeError(
                    f"ArXiv OCR request {request_id} differs from its immutable job payload"
                )
        return ActiveOcrBatch.model_validate(
            {
                "batch_id": str(first[0]),
                "state": str(first[1]),
                "provider": first[3],
                "provider_reference": str(first[4]),
                "provider_run_id": str(first[2]) if first[2] is not None else None,
                "job": job,
                "documents": tuple(
                    OcrBatchDocument(
                        request=request,
                        attempt_count=int(document_rows[request.request_id][12]),
                        state=OcrRunState(str(document_rows[request.request_id][13])),
                    )
                    for request in job.documents
                ),
            }
        )

    def candidates(
        self,
        executor: SqlExecutor,
        processor: ProcessorContract,
        *,
        configuration_hash: str,
        limit: int,
        arxiv_ids: tuple[str, ...] = (),
        verify_pdf: bool = False,
    ) -> tuple[OcrCandidate, ...]:
        if limit < 1 or limit > processor.batch.max_documents:
            raise ValueError("OCR candidate limit exceeds the processor batch contract")
        if verify_pdf and not arxiv_ids:
            raise ValueError("A forced PDF verification requires explicit ArXiv IDs")
        identifiers = ""
        parameters: list[Any] = [
            configuration_hash,
            OcrRunState.IMPORTED.value,
            configuration_hash,
            OcrRunState.IMPORTED.value,
            configuration_hash,
            verify_pdf,
            OcrRunState.RETRYABLE_FAILED.value,
            processor.retry.max_document_attempts,
        ]
        if arxiv_ids:
            identifiers = f"AND paper.arxiv_id IN ({', '.join('?' for _ in arxiv_ids)})"
            parameters.extend(arxiv_ids)
        result = executor.execute(
            f"""
            WITH latest_document AS (
                SELECT
                    request_id,
                    arxiv_id,
                    oai_datestamp,
                    source_record_sha256,
                    config_hash,
                    state,
                    attempt_count
                FROM (
                    SELECT
                        document.request_id,
                        document.arxiv_id,
                        document.oai_datestamp,
                        document.source_record_sha256,
                        document.config_hash,
                        document.state,
                        document.attempt_count,
                        row_number() OVER (
                            PARTITION BY
                                document.arxiv_id,
                                document.source_record_sha256,
                                document.config_hash
                            ORDER BY
                                document.attempt_count DESC,
                                document.prepared_at DESC,
                                document.batch_id DESC
                        ) AS attempt_rank
                    FROM {self._relation("ocr_document_runs")} AS document
                    JOIN {self._relation("ocr_batches")} AS batch
                      ON batch.batch_id = document.batch_id
                    WHERE document.config_hash = ?
                )
                WHERE attempt_rank = 1
            ),
            matching_success AS (
                SELECT DISTINCT
                    document.arxiv_id,
                    document.source_record_sha256
                FROM {self._relation("ocr_document_runs")} AS document
                WHERE document.state = ?
                  AND document.config_hash = ?
            ),
            reusable_success AS (
                SELECT
                    arxiv_id,
                    pdf_sha256,
                    pdf_size_bytes,
                    page_count,
                    processing_id,
                    manifest_sha256
                FROM (
                    SELECT
                        document.arxiv_id,
                        document.pdf_sha256,
                        document.pdf_size_bytes,
                        document.page_count,
                        document.processing_id,
                        document.manifest_sha256,
                        row_number() OVER (
                            PARTITION BY document.arxiv_id
                            ORDER BY
                                document.completed_at DESC,
                                document.curated_at DESC,
                                document.batch_id DESC
                        ) AS reuse_rank
                    FROM {self._relation("ocr_document_runs")} AS document
                    WHERE document.state = ?
                      AND document.config_hash = ?
                      AND document.pdf_sha256 IS NOT NULL
                      AND document.pdf_size_bytes IS NOT NULL
                      AND document.page_count IS NOT NULL
                      AND document.processing_id IS NOT NULL
                      AND document.artifact_uri IS NOT NULL
                      AND document.manifest_sha256 IS NOT NULL
                )
                WHERE reuse_rank = 1
            )
            SELECT
                paper.arxiv_id,
                paper.oai_datestamp,
                paper.source_record_sha256,
                paper.pdf_url,
                coalesce(document.attempt_count, 0) AS attempt_count,
                reusable.pdf_sha256,
                reusable.pdf_size_bytes,
                reusable.page_count,
                reusable.processing_id,
                reusable.manifest_sha256
            FROM {self._relation("papers")} AS paper
            LEFT JOIN latest_document AS document
              ON document.arxiv_id = paper.arxiv_id
             AND document.source_record_sha256 = paper.source_record_sha256
            LEFT JOIN matching_success AS success
              ON success.arxiv_id = paper.arxiv_id
             AND success.source_record_sha256 = paper.source_record_sha256
            LEFT JOIN reusable_success AS reusable
              ON reusable.arxiv_id = paper.arxiv_id
            WHERE NOT paper.is_deleted
              AND paper.pdf_url IS NOT NULL
              AND (
                    ?
                    OR (
                        success.arxiv_id IS NULL
                        AND (
                            document.request_id IS NULL
                            OR (
                                document.state = ?
                                AND document.attempt_count < ?
                            )
                        )
                    )
              )
              {identifiers}
            ORDER BY paper.oai_datestamp, paper.arxiv_id
            LIMIT {limit}
            """,
            parameters,
        )
        return tuple(
            OcrCandidate(
                arxiv_id=str(row[0]),
                oai_datestamp=row[1],
                source_record_sha256=str(row[2]),
                pdf_url=str(row[3]),
                attempt_count=int(row[4]),
                reuse=(
                    OcrReuseReference(
                        pdf_sha256=str(row[5]),
                        pdf_size_bytes=int(row[6]),
                        page_count=int(row[7]),
                        processing_id=str(row[8]),
                        manifest_sha256=str(row[9]),
                    )
                    if row[5] is not None
                    else None
                ),
            )
            for row in result.rows
        )

    def prepare_batch(
        self,
        executor: SqlExecutor,
        *,
        job: OcrJob,
        documents: tuple[OcrBatchDocument, ...],
        provider: str,
        provider_reference: str,
    ) -> None:
        attempts = {document.request.request_id: document.attempt_count for document in documents}
        for request in job.documents:
            executor.execute(
                f"""
                MERGE INTO {self._relation("ocr_document_runs")} AS target
                USING (
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ) AS source(
                    request_id,
                    batch_id,
                    arxiv_id,
                    oai_datestamp,
                    source_record_sha256,
                    pdf_url,
                    model_repository,
                    model_revision,
                    layout_model_repository,
                    layout_model_revision,
                    adapter_version,
                    config_hash
                )
                ON target.batch_id = source.batch_id
               AND target.request_id = source.request_id
                WHEN NOT MATCHED THEN INSERT (
                    request_id,
                    batch_id,
                    arxiv_id,
                    oai_datestamp,
                    source_record_sha256,
                    pdf_url,
                    pdf_sha256,
                    pdf_size_bytes,
                    page_count,
                    processing_id,
                    model_repository,
                    model_revision,
                    layout_model_repository,
                    layout_model_revision,
                    adapter_version,
                    config_hash,
                    artifact_uri,
                    manifest_sha256,
                    state,
                    attempt_count,
                    error_code,
                    error_message,
                    prepared_at,
                    submitted_at,
                    completed_at,
                    curated_at
                ) VALUES (
                    source.request_id,
                    source.batch_id,
                    source.arxiv_id,
                    source.oai_datestamp,
                    source.source_record_sha256,
                    source.pdf_url,
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    source.model_repository,
                    source.model_revision,
                    source.layout_model_repository,
                    source.layout_model_revision,
                    source.adapter_version,
                    source.config_hash,
                    NULL,
                    NULL,
                    '{OcrRunState.PREPARED.value}',
                    ?,
                    NULL,
                    NULL,
                    current_timestamp,
                    NULL,
                    NULL,
                    current_timestamp
                )
                """,
                (
                    request.request_id,
                    job.batch_id,
                    request.arxiv_id,
                    request.oai_datestamp,
                    request.source_record_sha256,
                    request.pdf_url,
                    job.model.repository,
                    job.model.revision,
                    job.layout_model.repository,
                    job.layout_model.revision,
                    job.adapter_version,
                    job.config_hash,
                    attempts[request.request_id],
                ),
            )
        # The batch row is the durable commit marker. Publishing it last prevents
        # a crash during document preparation from exposing an incomplete batch.
        job_json = job.model_dump_json()
        executor.execute(
            f"""
            MERGE INTO {self._relation("ocr_batches")} AS target
            USING (
                VALUES (?, ?, ?, ?, ?)
            ) AS source(batch_id, provider_reference, request_count, job_json, provider)
            ON target.batch_id = source.batch_id
            WHEN NOT MATCHED THEN INSERT (
                batch_id,
                provider_run_id,
                provider_reference,
                request_count,
                state,
                manifest_sha256,
                prepared_at,
                submitted_at,
                completed_at,
                error_code,
                error_message,
                curated_at,
                job_json,
                provider
            ) VALUES (
                source.batch_id,
                NULL,
                source.provider_reference,
                source.request_count,
                'prepared',
                NULL,
                current_timestamp,
                NULL,
                NULL,
                NULL,
                NULL,
                current_timestamp,
                source.job_json,
                source.provider
            )
            """,
            (job.batch_id, provider_reference, len(documents), job_json, provider),
        )

    def mark_batch_submitted(
        self,
        executor: SqlExecutor,
        *,
        batch_id: str,
        provider_run_id: str,
    ) -> None:
        executor.execute(
            f"""
            UPDATE {self._relation("ocr_document_runs")}
            SET state = ?,
                submitted_at = coalesce(submitted_at, current_timestamp),
                curated_at = current_timestamp
            WHERE batch_id = ?
              AND state IN (?, ?)
            """,
            (
                OcrRunState.SUBMITTED.value,
                batch_id,
                OcrRunState.PREPARED.value,
                OcrRunState.SUBMITTED.value,
            ),
        )
        executor.execute(
            f"""
            UPDATE {self._relation("ocr_batches")}
            SET provider_run_id = ?,
                state = 'submitted',
                submitted_at = coalesce(submitted_at, current_timestamp),
                curated_at = current_timestamp
            WHERE batch_id = ?
              AND state IN ('prepared', 'submitted')
            """,
            (provider_run_id, batch_id),
        )

    def mark_batch_running(self, executor: SqlExecutor, batch_id: str) -> None:
        executor.execute(
            f"""
            UPDATE {self._relation("ocr_document_runs")}
            SET state = ?,
                curated_at = current_timestamp
            WHERE batch_id = ?
              AND state IN (?, ?, ?)
            """,
            (
                OcrRunState.RUNNING.value,
                batch_id,
                OcrRunState.PREPARED.value,
                OcrRunState.SUBMITTED.value,
                OcrRunState.RUNNING.value,
            ),
        )
        executor.execute(
            f"""
            UPDATE {self._relation("ocr_batches")}
            SET state = 'running',
                curated_at = current_timestamp
            WHERE batch_id = ?
              AND state IN ('submitted', 'running')
            """,
            (batch_id,),
        )

    def mark_batch_terminal(
        self,
        executor: SqlExecutor,
        *,
        batch_id: str,
        state: Literal["completed", "failed"],
        manifest_sha256: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        executor.execute(
            f"""
            UPDATE {self._relation("ocr_batches")}
            SET state = ?,
                manifest_sha256 = ?,
                error_code = ?,
                error_message = ?,
                completed_at = current_timestamp,
                curated_at = current_timestamp
            WHERE batch_id = ?
            """,
            (state, manifest_sha256, error_code, error_message, batch_id),
        )

    def mark_document_failure(
        self,
        executor: SqlExecutor,
        *,
        batch_id: str,
        request_id: str,
        state: OcrRunState,
        error_code: str,
        error_message: str,
    ) -> None:
        if state not in {OcrRunState.RETRYABLE_FAILED, OcrRunState.TERMINAL_FAILED}:
            raise ValueError(f"Invalid OCR failure state: {state}")
        executor.execute(
            f"""
            UPDATE {self._relation("ocr_document_runs")}
            SET state = ?,
                error_code = ?,
                error_message = ?,
                completed_at = current_timestamp,
                curated_at = current_timestamp
            WHERE request_id = ?
              AND batch_id = ?
              AND state <> 'imported'
            """,
            (state.value, error_code, error_message[:2000], request_id, batch_id),
        )

    def processing_manifest(
        self,
        executor: SqlExecutor,
        processing_id: str,
    ) -> tuple[str, str] | None:
        result = executor.execute(
            f"""
            SELECT DISTINCT manifest_sha256, artifact_uri
            FROM {self._relation("ocr_document_runs")}
            WHERE processing_id = ?
              AND state = ?
            """,
            (processing_id, OcrRunState.IMPORTED.value),
        )
        values = {(str(row[0]), str(row[1])) for row in result.rows}
        if len(values) > 1:
            raise RuntimeError(
                f"Processing identity {processing_id} has conflicting imported manifests"
            )
        return next(iter(values)) if values else None

    def replace_elements(
        self,
        executor: SqlExecutor,
        *,
        processing_id: str,
        arxiv_id: str,
        elements: Sequence[OcrElement],
        chunk_size: int = 250,
    ) -> None:
        executor.execute(
            f"DELETE FROM {self._relation('ocr_document_elements')} WHERE processing_id = ?",
            (processing_id,),
        )
        for offset in range(0, len(elements), chunk_size):
            batch = elements[offset : offset + chunk_size]
            placeholders = ", ".join(
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)" for _ in batch
            )
            parameters: list[Any] = []
            for element in batch:
                parameters.extend(
                    (
                        processing_id,
                        element.element_id,
                        arxiv_id,
                        element.page_number,
                        element.reading_order,
                        element.element_type,
                        element.bbox_json,
                        element.text_content,
                        element.markdown_content,
                        element.parent_element_id,
                        element.raw_attributes_json,
                    )
                )
            executor.execute(
                f"""
                INSERT INTO {self._relation("ocr_document_elements")} (
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
                ) VALUES {placeholders}
                """,
                parameters,
            )

    def mark_document_imported(
        self,
        executor: SqlExecutor,
        *,
        batch_id: str,
        result: OcrDocumentResult,
        artifact_uri: str,
    ) -> None:
        executor.execute(
            f"""
            UPDATE {self._relation("ocr_document_runs")}
            SET pdf_sha256 = ?,
                pdf_size_bytes = ?,
                page_count = ?,
                processing_id = ?,
                artifact_uri = ?,
                manifest_sha256 = ?,
                state = ?,
                error_code = NULL,
                error_message = NULL,
                completed_at = current_timestamp,
                curated_at = current_timestamp
            WHERE request_id = ?
              AND batch_id = ?
            """,
            (
                result.pdf_sha256,
                result.pdf_size_bytes,
                result.page_count,
                result.processing_id,
                artifact_uri,
                result.manifest_sha256,
                OcrRunState.IMPORTED.value,
                result.request_id,
                batch_id,
            ),
        )
