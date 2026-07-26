"""Execute one bounded GLM-OCR batch and emit the canonical output protocol."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Iterable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fitz
import kagglehub
import requests
import yaml
import zstandard
from glmocr import GlmOcr

from mini_lakehouse.processing.ocr.core.files import file_sha256
from mini_lakehouse.processing.ocr.core.identity import (
    canonical_json_sha256,
    processing_id,
    successful_document_manifest_sha256,
)
from mini_lakehouse.processing.ocr.core.paths import runner_document_path
from mini_lakehouse.processing.ocr.core.protocol import (
    ArtifactFile,
    OcrBatchManifest,
    OcrDocumentRequest,
    OcrDocumentResult,
    OcrElement,
    OcrJob,
)

SOURCE_DIRECTORY = Path(__file__).resolve().parent
MODEL_MANIFEST_NAME = "mini_lakehouse_resource.json"
VLLM_DIAGNOSTIC_CHARACTERS = 32_000
VLLM_LOG_EVENT_CHARACTERS = 8_000
VLLM_METRIC_CHARACTERS = 16_000
VLLM_DIAGNOSTIC_METRICS = (
    "vllm:num_requests_running",
    "vllm:num_requests_waiting",
    "vllm:kv_cache_usage_perc",
    "vllm:request_queue_time_seconds",
    "vllm:time_to_first_token_seconds",
    "vllm:e2e_request_latency_seconds",
)
MEDIA_TYPES = {
    ".gz": "application/gzip",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".md": "text/markdown; charset=utf-8",
    ".png": "image/png",
}


class DocumentError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def log_stage(event: str, started_at: float, **fields: object) -> None:
    """Emit one machine-readable timing event to the Kaggle kernel log."""
    with suppress(OSError):
        print(
            json.dumps(
                {
                    "event": event,
                    "elapsed_seconds": round(time.perf_counter() - started_at, 3),
                    **fields,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            flush=True,
        )


def download_pdf(request: OcrDocumentRequest, destination: Path, max_bytes: int) -> tuple[str, int]:
    try:
        with requests.get(
            request.pdf_url,
            headers={"User-Agent": "mini-lakehouse-arxiv-ocr/1.0"},
            stream=True,
            timeout=(30, 180),
        ) as response:
            if response.status_code == 404:
                raise DocumentError("pdf_not_found", "ArXiv returned HTTP 404", retryable=False)
            if response.status_code == 429 or response.status_code >= 500:
                raise DocumentError(
                    "pdf_source_unavailable",
                    f"ArXiv returned HTTP {response.status_code}",
                    retryable=True,
                )
            response.raise_for_status()
            if not response.url.startswith("https://"):
                raise DocumentError(
                    "unsafe_pdf_redirect",
                    "ArXiv redirected the PDF to a non-HTTPS URL",
                    retryable=False,
                )
            declared_size = int(response.headers.get("content-length", "0"))
            if declared_size > max_bytes:
                raise DocumentError(
                    "pdf_too_large",
                    f"PDF declares {declared_size} bytes; maximum is {max_bytes}",
                    retryable=False,
                )
            digest = hashlib.sha256()
            size = 0
            with destination.open("xb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        raise DocumentError(
                            "pdf_too_large",
                            f"PDF exceeds the {max_bytes}-byte limit",
                            retryable=False,
                        )
                    digest.update(chunk)
                    output.write(chunk)
    except DocumentError:
        raise
    except (requests.RequestException, OSError) as error:
        raise DocumentError("pdf_download_failed", str(error), retryable=True) from error
    with destination.open("rb") as file:
        if file.read(5) != b"%PDF-":
            raise DocumentError("invalid_pdf", "Downloaded content is not a PDF", retryable=False)
    return digest.hexdigest(), size


def pdf_page_count(path: Path, maximum: int) -> int:
    try:
        with fitz.open(path) as document:
            pages = document.page_count
    except Exception as error:
        raise DocumentError("invalid_pdf", str(error), retryable=False) from error
    if pages < 1:
        raise DocumentError("empty_pdf", "PDF has no pages", retryable=False)
    if pages > maximum:
        raise DocumentError(
            "pdf_too_many_pages",
            f"PDF has {pages} pages; maximum is {maximum}",
            retryable=False,
        )
    return pages


def write_json_gzip(path: Path, value: Any) -> None:
    with (
        path.open("xb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        io.TextIOWrapper(compressed, encoding="utf-8") as output,
    ):
        json.dump(value, output, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def normalized_layout(
    value: Any,
    expected_pages: int,
) -> list[list[dict[str, Any]]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise DocumentError("invalid_model_output", str(error), retryable=True) from error
    if not isinstance(value, list):
        raise DocumentError(
            "invalid_model_output",
            "GLM-OCR JSON output must be a list of pages",
            retryable=True,
        )
    if len(value) != expected_pages:
        raise DocumentError(
            "incomplete_model_output",
            f"GLM-OCR returned {len(value)} pages for a {expected_pages}-page PDF",
            retryable=True,
        )
    pages: list[list[dict[str, Any]]] = []
    for page_index, raw_page in enumerate(value):
        if not isinstance(raw_page, list):
            raise DocumentError(
                "invalid_model_output",
                f"GLM-OCR page {page_index + 1} is not a list",
                retryable=True,
            )
        page: list[dict[str, Any]] = []
        for source_order, raw_block in enumerate(raw_page):
            if not isinstance(raw_block, dict):
                raise DocumentError(
                    "invalid_model_output",
                    f"GLM-OCR block {page_index + 1}:{source_order} is not an object",
                    retryable=True,
                )
            page.append(raw_block)
        pages.append(page)
    return pages


def canonical_elements(
    pages: list[list[dict[str, Any]]],
    current_processing_id: str,
) -> tuple[OcrElement, ...]:
    elements: list[OcrElement] = []
    for page_index, page in enumerate(pages):
        for reading_order, raw_block in enumerate(page):
            content = raw_block.get("content", "")
            if not isinstance(content, str):
                raise DocumentError(
                    "invalid_model_output",
                    f"GLM-OCR content {page_index + 1}:{reading_order} is not text",
                    retryable=True,
                )
            label = raw_block.get("label")
            if not isinstance(label, str) or not label:
                raise DocumentError(
                    "invalid_model_output",
                    f"GLM-OCR label {page_index + 1}:{reading_order} is missing",
                    retryable=True,
                )
            bbox = raw_block.get("bbox_2d")
            bbox_json = (
                json.dumps(bbox, separators=(",", ":"), ensure_ascii=False)
                if bbox is not None
                else None
            )
            raw_attributes = {
                key: raw_block[key]
                for key in sorted(raw_block)
                if key not in {"content", "bbox_2d", "index", "label"}
            }
            element_key = canonical_json_sha256(
                {
                    "page_number": page_index + 1,
                    "processing_id": current_processing_id,
                    "reading_order": reading_order,
                }
            )
            elements.append(
                OcrElement(
                    element_id=element_key,
                    page_number=page_index + 1,
                    reading_order=reading_order,
                    element_type=label,
                    bbox_json=bbox_json,
                    text_content=content,
                    markdown_content=content or None,
                    raw_attributes_json=(
                        json.dumps(
                            raw_attributes,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        if raw_attributes
                        else None
                    ),
                )
            )
    if not elements:
        raise DocumentError(
            "empty_model_output",
            "GLM-OCR produced no canonical elements",
            retryable=True,
        )
    return tuple(elements)


def write_elements(path: Path, elements: Iterable[OcrElement]) -> None:
    with (
        path.open("xb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        io.TextIOWrapper(compressed, encoding="utf-8") as output,
    ):
        for element in elements:
            output.write(element.model_dump_json())
            output.write("\n")


def copy_sdk_images(saved_root: Path, destination: Path) -> None:
    figures = saved_root / "imgs"
    if figures.is_dir():
        shutil.copytree(figures, destination / "figures")
    visualizations = saved_root / "layout_vis"
    if visualizations.is_dir():
        target = destination / "layout_vis"
        target.mkdir()
        for page_number, source in enumerate(sorted(visualizations.iterdir()), start=1):
            if source.is_file():
                shutil.copy2(source, target / f"page-{page_number:04d}{source.suffix.lower()}")


def artifact_files(root: Path, maximum_bytes: int) -> tuple[ArtifactFile, ...]:
    files: list[ArtifactFile] = []
    total = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        size = path.stat().st_size
        total += size
        if total > maximum_bytes:
            raise DocumentError(
                "ocr_output_too_large",
                f"OCR output exceeds the {maximum_bytes}-byte limit",
                retryable=False,
            )
        relative = path.relative_to(root).as_posix()
        files.append(
            ArtifactFile(
                relative_path=relative,
                sha256=file_sha256(path),
                size_bytes=size,
                media_type=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
            )
        )
    return tuple(files)


def successful_result(
    job: OcrJob,
    request: OcrDocumentRequest,
    parser: GlmOcr,
    work_root: Path,
    output_root: Path,
) -> OcrDocumentResult:
    started_at = time.perf_counter()
    pdf = work_root / "source.pdf"
    pdf_hash, pdf_size = download_pdf(request, pdf, job.limits.max_pdf_bytes)
    pages = pdf_page_count(pdf, job.limits.max_pages_per_document)
    current_processing_id = processing_id(
        arxiv_id=request.arxiv_id,
        pdf_sha256=pdf_hash,
        configuration_hash=job.config_hash,
    )
    try:
        parsed = parser.parse(pdf, save_layout_visualization=True)
    except Exception as error:
        raise DocumentError("ocr_inference_failed", str(error), retryable=True) from error

    document_root = work_root / "committed-output"
    document_root.mkdir()
    markdown = parsed.markdown_result or ""
    layout = normalized_layout(parsed.json_result, pages)
    elements = canonical_elements(layout, current_processing_id)
    (document_root / "document.md").write_text(markdown, encoding="utf-8")
    write_json_gzip(document_root / "layout.json.gz", layout)
    write_elements(document_root / "elements.jsonl.gz", elements)
    if parsed.raw_json_result is not None:
        write_json_gzip(document_root / "raw_model.json.gz", parsed.raw_json_result)

    saved = work_root / "sdk-output"
    parsed.save(output_dir=saved, save_layout_visualization=True)
    saved_directories = [path for path in saved.iterdir() if path.is_dir()]
    if len(saved_directories) == 1:
        copy_sdk_images(saved_directories[0], document_root)

    files = artifact_files(document_root, job.limits.max_output_bytes)
    payload: dict[str, Any] = {
        "request_id": request.request_id,
        "arxiv_id": request.arxiv_id,
        "state": "succeeded",
        "pdf_sha256": pdf_hash,
        "pdf_size_bytes": pdf_size,
        "page_count": pages,
        "processing_id": current_processing_id,
        "manifest_sha256": None,
        "error_code": None,
        "error_message": None,
        "files": [file.model_dump(mode="json") for file in files],
    }
    payload["manifest_sha256"] = successful_document_manifest_sha256(
        arxiv_id=request.arxiv_id,
        pdf_sha256=pdf_hash,
        pdf_size_bytes=pdf_size,
        page_count=pages,
        processing_id=current_processing_id,
        files=[file.model_dump(mode="json") for file in files],
    )
    result = OcrDocumentResult.model_validate(payload)
    destination = output_root.joinpath(
        *runner_document_path(request.arxiv_id, request.request_id).parts
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    document_root.replace(destination)
    log_stage(
        "document_succeeded",
        started_at,
        arxiv_id=request.arxiv_id,
        page_count=pages,
        request_id=request.request_id,
    )
    return result


def failed_result(request: OcrDocumentRequest, error: DocumentError) -> OcrDocumentResult:
    return OcrDocumentResult(
        request_id=request.request_id,
        arxiv_id=request.arxiv_id,
        state="retryable_failed" if error.retryable else "terminal_failed",
        error_code=error.code,
        error_message=str(error)[:2000],
    )


def write_config(path: Path, job: OcrJob, layout_model_path: str) -> None:
    config = {
        "logging": {"level": "INFO"},
        "pipeline": {
            "maas": {"enabled": False},
            "max_workers": job.inference.max_workers,
            "ocr_api": {
                "api_host": "127.0.0.1",
                "api_port": job.inference.api_port,
                "model": "glm-ocr",
                "request_timeout": job.inference.request_timeout_seconds,
            },
            "page_loader": {"pdf_max_pages": job.limits.max_pages_per_document},
            "layout": {
                "model_dir": layout_model_path,
                "device": job.inference.layout_device,
            },
        },
    }
    path.write_text(yaml.safe_dump(config, sort_keys=True), encoding="utf-8")


def resolve_model_source(
    source: str,
    *,
    resource_name: str,
    repository: str,
    revision: str,
) -> Path:
    path = Path(kagglehub.model_download(source))
    try:
        manifest = json.loads((path / MODEL_MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Kaggle model {source!r} has an invalid resource manifest") from error
    expected = {
        "resource_name": resource_name,
        "repository": repository,
        "revision": revision,
    }
    expected_identity = canonical_json_sha256(expected)
    if (
        any(manifest.get(name) != value for name, value in expected.items())
        or manifest.get("identity_sha256") != expected_identity
    ):
        raise RuntimeError(f"Kaggle model {source!r} does not match the OCR job")
    return path


def start_vllm(
    job: OcrJob,
    model_path: Path,
    log_path: Path,
) -> tuple[subprocess.Popen[bytes], Any]:
    log_file = log_path.open("wb")
    command = [
        sys.executable,
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        str(model_path),
        "--served-model-name",
        "glm-ocr",
        "--port",
        str(job.inference.api_port),
        "--dtype",
        job.inference.dtype,
        "--max-model-len",
        str(job.inference.max_model_len),
        "--gpu-memory-utilization",
        str(job.inference.gpu_memory_utilization),
        "--max-num-seqs",
        str(job.inference.max_num_seqs),
    ]
    if job.inference.enforce_eager:
        command.append("--enforce-eager")
    if job.inference.speculative_tokens:
        command.extend(
            [
                "--speculative-config",
                json.dumps(
                    {
                        "method": "mtp",
                        "num_speculative_tokens": job.inference.speculative_tokens,
                    },
                    separators=(",", ":"),
                ),
            ]
        )
    process = subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT)
    endpoint = f"http://127.0.0.1:{job.inference.api_port}/v1/models"
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        if process.poll() is not None:
            diagnostic = log_path.read_text(encoding="utf-8", errors="replace")[
                -VLLM_DIAGNOSTIC_CHARACTERS:
            ]
            log_file.close()
            raise RuntimeError(
                f"vLLM exited with status {process.returncode}; log tail: {diagnostic}"
            )
        try:
            if requests.get(endpoint, timeout=5).ok:
                return process, log_file
        except requests.RequestException:
            pass
        time.sleep(5)
    stop_process(process, log_file)
    raise RuntimeError("vLLM did not become ready within 15 minutes")


def stop_process(process: subprocess.Popen[bytes], log_file: Any) -> None:
    """Bounded, reap-complete shutdown for the local model server."""
    try:
        if process.poll() is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    process.kill()
                process.wait(timeout=30)
    finally:
        log_file.close()


def create_archive(source: Path, destination: Path) -> None:
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
                relative = path.relative_to(source).as_posix()
                info = tarfile.TarInfo(relative)
                info.size = path.stat().st_size
                info.mode = 0o644
                info.mtime = 0
                with path.open("rb") as file:
                    bundle.addfile(info, file)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def write_text_atomic(destination: Path, content: str) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.partial")
    temporary.unlink(missing_ok=True)
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _vllm_log_tail(log_path: Path) -> str:
    try:
        return log_path.read_text(encoding="utf-8", errors="replace")[-VLLM_DIAGNOSTIC_CHARACTERS:]
    except OSError as error:
        return f"vLLM log unavailable: {error}"


def _vllm_metrics(api_port: int) -> str:
    try:
        response = requests.get(f"http://127.0.0.1:{api_port}/metrics", timeout=5)
        response.raise_for_status()
    except requests.RequestException as error:
        return f"vLLM metrics unavailable: {error}"
    metrics = "\n".join(
        line
        for line in response.text.splitlines()
        if not line.startswith("#") and any(name in line for name in VLLM_DIAGNOSTIC_METRICS)
    )
    return metrics[-VLLM_METRIC_CHARACTERS:] or "No selected vLLM metrics were reported"


def persist_vllm_diagnostic(
    output_directory: Path,
    log_path: Path,
    job: OcrJob,
    *,
    error: BaseException,
    request: OcrDocumentRequest | None,
) -> None:
    """Persist bounded server evidence and mirror a useful tail to the kernel log."""
    log_tail = _vllm_log_tail(log_path)
    payload = {
        "batch_id": job.batch_id,
        "captured_at": datetime.now(UTC).isoformat(),
        "error": str(error)[:2000],
        "request_id": None if request is None else request.request_id,
        "arxiv_id": None if request is None else request.arxiv_id,
        "metrics": _vllm_metrics(job.inference.api_port),
        "vllm_log_tail": log_tail,
    }
    diagnostics = output_directory / "diagnostics"
    name = "runtime" if request is None else request.request_id
    diagnostic_file: str | None = f"diagnostics/{name}.json"
    try:
        diagnostics.mkdir(parents=True, exist_ok=True)
        write_text_atomic(
            diagnostics / f"{name}.json",
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
    except OSError as diagnostic_error:
        diagnostic_file = None
        payload["persistence_error"] = str(diagnostic_error)
    print(
        json.dumps(
            {
                **payload,
                "event": "vllm_diagnostic",
                "diagnostic_file": diagnostic_file,
                "vllm_log_tail": log_tail[-VLLM_LOG_EVENT_CHARACTERS:],
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
    model_source: str,
    layout_model_source: str,
) -> None:
    batch_started_at = time.perf_counter()
    output_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="glm-ocr-") as raw_temp:
        temp = Path(raw_temp)
        extracted = temp / "result"
        extracted.mkdir()
        model_started_at = time.perf_counter()
        model_path = resolve_model_source(
            model_source,
            resource_name="model",
            repository=job.model.repository,
            revision=job.model.revision,
        )
        layout_path = resolve_model_source(
            layout_model_source,
            resource_name="layout_model",
            repository=job.layout_model.repository,
            revision=job.layout_model.revision,
        )
        log_stage("models_resolved", model_started_at, batch_id=job.batch_id)
        config_path = temp / "glmocr.yaml"
        write_config(config_path, job, str(layout_path))
        server_started_at = time.perf_counter()
        vllm_log_path = temp / "vllm.log"
        vllm, log_file = start_vllm(job, model_path, vllm_log_path)
        log_stage("vllm_ready", server_started_at, batch_id=job.batch_id)
        results: list[OcrDocumentResult] = []
        try:
            with GlmOcr(
                config_path=str(config_path), layout_device=job.inference.layout_device
            ) as parser:
                for request in job.documents:
                    document_started_at = time.perf_counter()
                    document_work = temp / "work" / request.request_id
                    document_work.mkdir(parents=True)
                    try:
                        result = successful_result(
                            job,
                            request,
                            parser,
                            document_work,
                            extracted,
                        )
                    except DocumentError as error:
                        persist_vllm_diagnostic(
                            output_directory,
                            vllm_log_path,
                            job,
                            error=error,
                            request=request,
                        )
                        result = failed_result(request, error)
                        log_stage(
                            "document_failed",
                            document_started_at,
                            arxiv_id=request.arxiv_id,
                            error_code=error.code,
                            request_id=request.request_id,
                        )
                    except Exception as error:
                        document_error = DocumentError(
                            "unexpected_runner_error",
                            str(error),
                            retryable=True,
                        )
                        persist_vllm_diagnostic(
                            output_directory,
                            vllm_log_path,
                            job,
                            error=document_error,
                            request=request,
                        )
                        result = failed_result(request, document_error)
                        log_stage(
                            "document_failed",
                            document_started_at,
                            arxiv_id=request.arxiv_id,
                            error_code="unexpected_runner_error",
                            request_id=request.request_id,
                        )
                    results.append(result)
        except Exception as error:
            persist_vllm_diagnostic(
                output_directory,
                vllm_log_path,
                job,
                error=error,
                request=None,
            )
            raise
        finally:
            stop_process(vllm, log_file)

        manifest_path = output_directory / "result_manifest.json"
        manifest_path.unlink(missing_ok=True)
        archive = output_directory / "result.tar.zst"
        archive_started_at = time.perf_counter()
        create_archive(extracted, archive)
        log_stage("archive_created", archive_started_at, batch_id=job.batch_id)
        manifest = OcrBatchManifest(
            schema_version=job.schema_version,
            batch_id=job.batch_id,
            created_at=datetime.now(UTC),
            archive_sha256=file_sha256(archive),
            archive_size_bytes=archive.stat().st_size,
            documents=tuple(results),
        )
        write_text_atomic(
            manifest_path,
            manifest.model_dump_json(indent=2),
        )
        log_stage(
            "batch_committed",
            batch_started_at,
            batch_id=job.batch_id,
            document_count=len(results),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--model-source", required=True)
    parser.add_argument("--layout-model-source", required=True)
    arguments = parser.parse_args()
    job = OcrJob.model_validate_json((SOURCE_DIRECTORY / "job.json").read_bytes())
    run(
        job,
        arguments.output_directory,
        model_source=arguments.model_source,
        layout_model_source=arguments.layout_model_source,
    )


if __name__ == "__main__":
    main()
