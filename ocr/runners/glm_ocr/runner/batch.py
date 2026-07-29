"""Batch orchestration and commit protocol for a GLM-OCR runner."""

import json
import os
import tarfile
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import zstandard
from document_ocr.identity import file_sha256
from document_ocr.protocol import (
    OcrBatchManifest,
    OcrDocumentRequest,
    OcrDocumentResult,
    OcrJob,
)

from .documents import (
    DocumentError,
    PreparedDocument,
    failed_result,
    prepare_document,
    process_document,
)
from .engine import InferenceEngine


def emit(event: str, started_at: float, **fields: object) -> None:
    """Emit a compact JSON event that both remote providers can stream."""
    with suppress(OSError):
        print(
            json.dumps(
                {
                    "event": event,
                    "elapsed_seconds": round(time.perf_counter() - started_at, 3),
                    **fields,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            flush=True,
        )


def _progress(
    job: OcrJob,
    results: dict[str, OcrDocumentResult],
    started_at: float,
) -> None:
    values = tuple(results.values())
    emit(
        "batch_progress",
        started_at,
        batch_id=job.batch_id,
        completed_documents=len(values),
        document_count=len(job.documents),
        failed_documents=sum(
            result.state in {"retryable_failed", "terminal_failed"} for result in values
        ),
        reused_documents=sum(result.state == "reused" for result in values),
        succeeded_documents=sum(result.state == "succeeded" for result in values),
    )


def _failure(
    prepared_or_request: PreparedDocument | OcrDocumentRequest,
    error: DocumentError,
    *,
    document_count: int,
    document_index: int,
    started_at: float,
) -> None:
    request = (
        prepared_or_request.request
        if isinstance(prepared_or_request, PreparedDocument)
        else prepared_or_request
    )
    emit(
        "document_failed",
        started_at,
        arxiv_id=request.arxiv_id,
        document_count=document_count,
        document_index=document_index,
        error_code=error.code,
        error_message=str(error)[:2_000],
        request_id=request.request_id,
        retryable=error.retryable,
    )


def _create_archive(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.partial")
    temporary.unlink(missing_ok=True)
    try:
        with (
            temporary.open("xb") as raw_output,
            zstandard.ZstdCompressor(level=9, write_checksum=True).stream_writer(
                raw_output
            ) as output,
            tarfile.open(fileobj=output, mode="w|") as bundle,
        ):
            for path in sorted(item for item in source.rglob("*") if item.is_file()):
                info = tarfile.TarInfo(path.relative_to(source).as_posix())
                info.size = path.stat().st_size
                info.mode = 0o644
                info.mtime = 0
                with path.open("rb") as file:
                    bundle.addfile(info, file)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _write_manifest(destination: Path, manifest: OcrBatchManifest) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.partial")
    temporary.unlink(missing_ok=True)
    try:
        temporary.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def run(
    job: OcrJob,
    output_directory: Path,
    *,
    model_path: Path,
    layout_model_path: Path,
    engine: InferenceEngine | None = None,
) -> None:
    """Run all documents and make the batch visible by committing its manifest last."""
    batch_started_at = time.perf_counter()
    emit(
        "batch_started",
        batch_started_at,
        arxiv_ids=[request.arxiv_id for request in job.documents],
        batch_id=job.batch_id,
        document_count=len(job.documents),
        enforce_eager=job.inference.enforce_eager,
        max_num_seqs=job.inference.max_num_seqs,
        max_workers=job.inference.max_workers,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    manifest_path = output_directory / "result_manifest.json"
    manifest_path.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="glm-ocr-") as temporary_directory:
        temporary = Path(temporary_directory)
        extracted = temporary / "result"
        extracted.mkdir()
        document_count = len(job.documents)
        prepared_documents: list[tuple[PreparedDocument, int, float]] = []
        results: dict[str, OcrDocumentResult] = {}

        for document_index, request in enumerate(job.documents, start=1):
            document_started_at = time.perf_counter()
            emit(
                "document_started",
                document_started_at,
                arxiv_id=request.arxiv_id,
                document_count=document_count,
                document_index=document_index,
                request_id=request.request_id,
            )
            document_work = temporary / "work" / request.request_id
            document_work.mkdir(parents=True)
            try:
                prepared = prepare_document(job, request, document_work)
                emit(
                    "document_loaded",
                    document_started_at,
                    arxiv_id=request.arxiv_id,
                    document_count=document_count,
                    document_index=document_index,
                    page_count=prepared.page_count,
                    pdf_size_bytes=prepared.pdf_size_bytes,
                    request_id=request.request_id,
                )
                if isinstance(prepared, OcrDocumentResult):
                    results[request.request_id] = prepared
                    emit(
                        "document_reused",
                        document_started_at,
                        arxiv_id=request.arxiv_id,
                        document_count=document_count,
                        document_index=document_index,
                        page_count=prepared.page_count,
                        request_id=request.request_id,
                    )
                else:
                    prepared_documents.append((prepared, document_index, document_started_at))
            except DocumentError as error:
                results[request.request_id] = failed_result(request, error)
                _failure(
                    request,
                    error,
                    document_count=document_count,
                    document_index=document_index,
                    started_at=document_started_at,
                )
            if request.request_id in results:
                _progress(job, results, batch_started_at)

        if prepared_documents:
            if not model_path.is_dir() or not layout_model_path.is_dir():
                raise FileNotFoundError("OCR model paths must be existing directories")
            inference_engine = engine or InferenceEngine(temporary / "engine")
            server_started_at = time.perf_counter()
            parser = inference_engine.acquire(
                job,
                model_path=model_path,
                layout_model_path=layout_model_path,
            )
            emit("inference_ready", server_started_at, batch_id=job.batch_id)
            try:
                for prepared, document_index, document_started_at in prepared_documents:
                    try:
                        result = process_document(job, prepared, parser, extracted)
                        emit(
                            "document_succeeded",
                            document_started_at,
                            arxiv_id=prepared.request.arxiv_id,
                            document_count=document_count,
                            document_index=document_index,
                            page_count=prepared.page_count,
                            request_id=prepared.request.request_id,
                        )
                    except DocumentError as error:
                        result = failed_result(prepared.request, error)
                        _failure(
                            prepared,
                            error,
                            document_count=document_count,
                            document_index=document_index,
                            started_at=document_started_at,
                        )
                    results[prepared.request.request_id] = result
                    _progress(job, results, batch_started_at)
            except Exception:
                if engine is not None:
                    engine.close()
                raise
            finally:
                if engine is None:
                    inference_engine.close()
        else:
            emit(
                "inference_skipped",
                batch_started_at,
                batch_id=job.batch_id,
                reused_documents=sum(result.state == "reused" for result in results.values()),
            )

        archive = output_directory / "result.tar.zst"
        archive_started_at = time.perf_counter()
        _create_archive(extracted, archive)
        emit("archive_created", archive_started_at, batch_id=job.batch_id)
        _write_manifest(
            manifest_path,
            OcrBatchManifest(
                schema_version=job.schema_version,
                batch_id=job.batch_id,
                created_at=datetime.now(UTC),
                archive_sha256=file_sha256(archive),
                archive_size_bytes=archive.stat().st_size,
                documents=tuple(results[request.request_id] for request in job.documents),
            ),
        )
        emit(
            "batch_committed",
            batch_started_at,
            batch_id=job.batch_id,
            document_count=len(job.documents),
        )
