"""Single-document execution and commit protocol for the Modal worker."""

import json
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Annotated

import typer
from document_ocr.artifacts import commit_run_output, reset_run_output
from document_ocr.protocol import (
    GlmOcrJob,
    OcrDocumentManifest,
    OcrDocumentResult,
)
from document_ocr.source import prepare_document

from .document import process_document
from .engine import InferenceEngine


def emit(event: str, started_at: float, **fields: object) -> None:
    """Emit a compact JSON event that Modal can stream to the OCR workflow."""
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
    job: GlmOcrJob,
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
    reset_run_output(output_directory)

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

        archive_started_at = time.perf_counter()
        commit_run_output(job, result, document_manifest, artifacts, output_directory)
        emit("archive_created", archive_started_at, run_id=job.run_id)
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
        GlmOcrJob.model_validate_json(job.read_bytes()),
        output_directory,
        model_path=model_path,
        layout_model_path=layout_model_path,
    )


if __name__ == "__main__":
    typer.run(main)
