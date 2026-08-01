"""Coordinate one curated ArXiv document across a remote OCR provider."""

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from loguru import logger

from document_ocr.arxiv.store import IMPORTED_STATE, ArxivOcrStore
from document_ocr.config import OcrConfig
from document_ocr.identity import request_id
from document_ocr.output import extract_ocr_output
from document_ocr.protocol import OcrDocumentRequest, OcrJob, OcrReuseReference
from document_ocr.providers.base import (
    OcrProvider,
    OcrProviderError,
    OcrProviderRunFailedError,
    OcrRunNotFoundError,
)

PROCESSING_STATE = "processing"


class OcrError(RuntimeError):
    pass


class ArxivOcrWorkflow:
    """Own one crash-safe ArXiv PDF execution."""

    def __init__(
        self,
        *,
        store: ArxivOcrStore,
        processor: OcrConfig,
        provider: OcrProvider,
    ) -> None:
        self._store = store
        self._processor = processor
        self._provider = provider

    @staticmethod
    def _latest(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        return max(
            rows,
            key=lambda row: (
                row.get("attempt") or 0,
                row.get("started_at") or datetime.min.replace(tzinfo=UTC),
                row.get("run_id") or "",
            ),
            default=None,
        )

    def _reuse(self, rows: list[dict[str, Any]]) -> OcrReuseReference | None:
        reusable = [
            row
            for row in rows
            if row["state"] == IMPORTED_STATE
            and row["config_hash"] == self._processor.configuration_hash
            and all(
                row.get(field) is not None
                for field in (
                    "pdf_sha256",
                    "pdf_size_bytes",
                    "page_count",
                    "processing_id",
                    "manifest_sha256",
                )
            )
        ]
        latest = max(
            reusable,
            key=lambda row: (
                row.get("completed_at") or datetime.min.replace(tzinfo=UTC),
                row["run_id"],
            ),
            default=None,
        )
        if latest is None:
            return None
        return OcrReuseReference(
            pdf_sha256=latest["pdf_sha256"],
            pdf_size_bytes=latest["pdf_size_bytes"],
            page_count=latest["page_count"],
            processing_id=latest["processing_id"],
            manifest_sha256=latest["manifest_sha256"],
        )

    def _job(self, arxiv_id: str) -> tuple[OcrJob | None, dict[str, Any]]:
        try:
            paper = self._store.paper(arxiv_id)
        except ValueError as error:
            raise OcrError(str(error)) from error
        if paper["is_deleted"]:
            raise OcrError(f"ArXiv paper {arxiv_id!r} is deleted")
        rows = self._store.runs(arxiv_id)
        request_key = request_id(
            document_id=arxiv_id,
            source_record_sha256=paper["source_record_sha256"],
            configuration_hash=self._processor.configuration_hash,
        )
        attempts = [row for row in rows if row["request_id"] == request_key]
        latest = self._latest(attempts)
        if latest is not None and latest["state"] == IMPORTED_STATE:
            return None, latest
        if latest is not None and latest["state"] == PROCESSING_STATE:
            job = OcrJob.model_validate_json(latest["job_json"])
            if (
                job.run_id != latest["run_id"]
                or latest["provider"] != self._provider.name
                or latest["provider_reference"] != self._provider.reference
            ):
                raise OcrError("Persisted OCR execution ownership has drifted")
            return job, latest

        attempt = max((int(row["attempt"]) for row in attempts), default=0) + 1
        request = OcrDocumentRequest(
            request_id=request_key,
            document_id=arxiv_id,
            source_updated_date=paper["oai_datestamp"],
            source_record_sha256=paper["source_record_sha256"],
            pdf_url=paper["pdf_url"],
            reuse=self._reuse(rows),
        )
        job = self._processor.build_job(request, attempt=attempt)
        now = datetime.now(UTC)
        run = {
            "run_id": job.run_id,
            "request_id": request.request_id,
            "arxiv_id": arxiv_id,
            "oai_datestamp": paper["oai_datestamp"],
            "source_record_sha256": request.source_record_sha256,
            "pdf_url": request.pdf_url,
            "pdf_sha256": None,
            "pdf_size_bytes": None,
            "page_count": None,
            "processing_id": None,
            "model_repository": job.model.repository,
            "model_revision": job.model.revision,
            "layout_model_repository": job.layout_model.repository,
            "layout_model_revision": job.layout_model.revision,
            "adapter_version": job.adapter_version,
            "config_hash": job.config_hash,
            "artifact_uri": None,
            "manifest_sha256": None,
            "state": PROCESSING_STATE,
            "attempt": attempt,
            "started_at": now,
            "completed_at": None,
            "curated_at": now,
            "provider": self._provider.name,
            "provider_reference": self._provider.reference,
            "provider_run_id": None,
            "job_json": job.model_dump_json(),
        }
        self._store.save_run(run)
        return job, run

    def _mark_failed(self, run: dict[str, Any]) -> None:
        run.update(
            state="failed",
            completed_at=datetime.now(UTC),
        )
        self._store.save_run(run)

    def _log(self, message: str) -> None:
        for line in message.rstrip().splitlines():
            if line:
                logger.info("[{}] {}", self._provider.name, line)

    def _wait(self, run: dict[str, Any]) -> None:
        provider_run_id = run["provider_run_id"]
        if not isinstance(provider_run_id, str):
            raise RuntimeError("Submitted OCR run has no provider execution ID")
        try:
            self._provider.wait(provider_run_id, self._log)
        except OcrRunNotFoundError as error:
            self._mark_failed(run)
            raise OcrError(str(error)) from error
        except OcrProviderRunFailedError as error:
            self._mark_failed(run)
            raise OcrError(str(error)) from error
        except OcrProviderError as error:
            raise OcrError(str(error)) from error

    def run(self, arxiv_id: str) -> dict[str, Any]:
        job, run = self._job(arxiv_id)
        if job is None:
            logger.info("ArXiv {} already has imported OCR for this revision", arxiv_id)
            return run
        if run["provider_run_id"] is None:
            provider_run_id = self._provider.submit(job)
            run.update(
                provider_run_id=provider_run_id,
            )
            self._store.save_run(run)
        self._wait(run)

        with TemporaryDirectory(prefix="arxiv-ocr-") as temporary_directory:
            temporary = Path(temporary_directory)
            output = temporary / "provider"
            provider_run_id = run["provider_run_id"]
            if not isinstance(provider_run_id, str):
                raise RuntimeError("Submitted OCR run has no provider execution ID")
            self._provider.download_output(provider_run_id, output)
            extracted = temporary / "artifacts"
            output_result = extract_ocr_output(output, extracted, job=job)
            result = output_result.result
            self._store.publish(job=job, run=run, extracted=extracted, result=result)
        logger.info("Imported OCR for ArXiv {} as {}", arxiv_id, run["processing_id"])
        return run
