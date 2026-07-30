"""Single-document execution and commit protocol for the GLM-OCR runner."""

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
    OcrDocumentResult,
    OcrJob,
    OcrRunManifest,
)

from .document import (
    DocumentError,
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


def _failure(job: OcrJob, error: DocumentError, started_at: float) -> None:
    emit(
        "document_failed",
        started_at,
        document_id=job.document.document_id,
        error_code=error.code,
        error_message=str(error)[:2_000],
        request_id=job.document.request_id,
        run_id=job.run_id,
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


def _write_manifest(destination: Path, manifest: OcrRunManifest) -> None:
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
    """Run one PDF and make its output visible by committing the manifest last."""
    started_at = time.perf_counter()
    request = job.document
    emit(
        "run_started",
        started_at,
        document_id=request.document_id,
        enforce_eager=job.inference.enforce_eager,
        max_num_seqs=job.inference.max_num_seqs,
        max_workers=job.inference.max_workers,
        request_id=request.request_id,
        run_id=job.run_id,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    manifest_path = output_directory / "result_manifest.json"
    manifest_path.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="glm-ocr-") as temporary_directory:
        temporary = Path(temporary_directory)
        artifacts = temporary / "artifacts"
        work = temporary / "work"
        work.mkdir()
        try:
            prepared = prepare_document(job, request, work)
        except DocumentError as error:
            result = failed_result(request, error)
            _failure(job, error, started_at)
        else:
            emit(
                "document_loaded",
                started_at,
                document_id=request.document_id,
                page_count=prepared.page_count,
                pdf_size_bytes=prepared.pdf_size_bytes,
                request_id=request.request_id,
                run_id=job.run_id,
            )
            if isinstance(prepared, OcrDocumentResult):
                result = prepared
                emit(
                    "document_reused",
                    started_at,
                    document_id=request.document_id,
                    page_count=prepared.page_count,
                    request_id=request.request_id,
                    run_id=job.run_id,
                )
            else:
                if not model_path.is_dir() or not layout_model_path.is_dir():
                    raise FileNotFoundError("OCR model paths must be existing directories")
                inference_engine = engine or InferenceEngine(temporary / "engine")
                owns_engine = engine is None
                server_started_at = time.perf_counter()
                try:
                    parser = inference_engine.acquire(
                        job,
                        model_path=model_path,
                        layout_model_path=layout_model_path,
                    )
                    emit("inference_ready", server_started_at, run_id=job.run_id)
                    try:
                        result = process_document(job, prepared, parser, artifacts)
                    except DocumentError as error:
                        result = failed_result(request, error)
                        _failure(job, error, started_at)
                    else:
                        emit(
                            "document_succeeded",
                            started_at,
                            document_id=request.document_id,
                            page_count=prepared.page_count,
                            request_id=request.request_id,
                            run_id=job.run_id,
                        )
                except Exception:
                    inference_engine.close()
                    raise
                finally:
                    if owns_engine:
                        inference_engine.close()

        artifacts.mkdir(exist_ok=True)
        archive = output_directory / "result.tar.zst"
        archive_started_at = time.perf_counter()
        _create_archive(artifacts, archive)
        emit("archive_created", archive_started_at, run_id=job.run_id)
        _write_manifest(
            manifest_path,
            OcrRunManifest(
                run_id=job.run_id,
                created_at=datetime.now(UTC),
                archive_sha256=file_sha256(archive),
                archive_size_bytes=archive.stat().st_size,
                result=result,
            ),
        )
        emit(
            "run_committed",
            started_at,
            document_id=request.document_id,
            run_id=job.run_id,
            state=result.state,
        )
