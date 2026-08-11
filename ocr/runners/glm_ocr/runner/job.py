"""Single-document execution and commit protocol for the GLM-OCR runner."""

import json
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from document_ocr.artifacts import create_archive, write_run_result
from document_ocr.identity import file_sha256
from document_ocr.output import OCR_ARCHIVE_FILE, OCR_RESULT_FILE
from document_ocr.protocol import (
    OcrDocumentManifest,
    OcrDocumentResult,
    OcrJob,
    OcrRunResult,
)

from .document import (
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
    result_path = output_directory / OCR_RESULT_FILE
    archive = output_directory / OCR_ARCHIVE_FILE
    result_path.unlink(missing_ok=True)
    archive.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="glm-ocr-") as temporary_directory:
        temporary = Path(temporary_directory)
        artifacts = temporary / "artifacts"
        document_manifest: OcrDocumentManifest | None = None
        work = temporary / "work"
        work.mkdir()
        prepared = prepare_document(job, work)
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
                result, document_manifest = process_document(
                    job,
                    prepared,
                    parser,
                    artifacts,
                )
                emit(
                    "document_succeeded",
                    started_at,
                    document_id=request.document_id,
                    page_count=prepared.page_count,
                    request_id=request.request_id,
                    run_id=job.run_id,
                )
            except Exception:
                if not owns_engine:
                    inference_engine.close()
                raise
            finally:
                if owns_engine:
                    inference_engine.close()

        artifacts.mkdir(exist_ok=True)
        archive_started_at = time.perf_counter()
        create_archive(artifacts, archive)
        emit("archive_created", archive_started_at, run_id=job.run_id)
        write_run_result(
            result_path,
            OcrRunResult(
                run_id=job.run_id,
                created_at=datetime.now(UTC),
                archive_sha256=file_sha256(archive),
                archive_size_bytes=archive.stat().st_size,
                result=result,
                document=document_manifest,
            ),
        )
        emit(
            "run_committed",
            started_at,
            document_id=request.document_id,
            run_id=job.run_id,
            state=result.state,
        )


def main(
    job: Annotated[Path, typer.Option(exists=True, dir_okay=False, readable=True)],
    output_directory: Annotated[Path, typer.Option(file_okay=False)],
    model_path: Annotated[Path, typer.Option(exists=True, file_okay=False, readable=True)],
    layout_model_path: Annotated[
        Path,
        typer.Option(exists=True, file_okay=False, readable=True),
    ],
) -> None:
    run(
        OcrJob.model_validate_json(job.read_bytes()),
        output_directory,
        model_path=model_path,
        layout_model_path=layout_model_path,
    )


if __name__ == "__main__":
    typer.run(main)
