"""Execute one provider-neutral GLM-OCR batch and emit the canonical output protocol."""

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Iterable
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import fitz
import requests
import yaml
import zstandard
from glmocr import GlmOcr
from glmocr.config import GlmOcrConfig
from progress import GlmOcrPageProgress
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from mini_lakehouse.processing.ocr.core.files import file_sha256
from mini_lakehouse.processing.ocr.core.identity import (
    canonical_json_sha256,
    processing_id,
    successful_document_manifest_sha256,
)
from mini_lakehouse.processing.ocr.core.paths import (
    PAGE_MARKDOWN_BUNDLE_PATH,
    runner_document_path,
)
from mini_lakehouse.processing.ocr.core.protocol import (
    ArtifactFile,
    OcrBatchManifest,
    OcrDocumentRequest,
    OcrDocumentResult,
    OcrElement,
    OcrJob,
    OcrPageMarkdownBundle,
)
from mini_lakehouse.processing.ocr.core.text import build_page_markdown_bundle

SOURCE_DIRECTORY = Path(__file__).resolve().parent
MODEL_MANIFEST_NAME = "mini_lakehouse_resource.json"
VLLM_DIAGNOSTIC_CHARACTERS = 32_000
VLLM_LOG_EVENT_CHARACTERS = 8_000
VLLM_METRIC_CHARACTERS = 16_000
ERROR_MESSAGE_CHARACTERS = 2_000
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


def _create_pdf_session() -> requests.Session:
    retry = Retry(
        total=4,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    session.headers["User-Agent"] = "mini-lakehouse-arxiv-ocr/1.0"
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


_PDF_SESSION = _create_pdf_session()


class DocumentError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class PreparedDocument:
    request: OcrDocumentRequest
    work_root: Path
    pdf_path: Path
    pdf_sha256: str
    pdf_size_bytes: int
    page_count: int
    document_count: int
    document_index: int
    started_at: float


def log_stage(event: str, started_at: float, **fields: object) -> None:
    """Emit one machine-readable timing event to the provider log."""
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
        with _PDF_SESSION.get(
            request.pdf_url,
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


def write_page_markdown_bundle(path: Path, bundle: OcrPageMarkdownBundle) -> None:
    with (
        path.open("xb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        io.TextIOWrapper(compressed, encoding="utf-8") as output,
    ):
        output.write(bundle.model_dump_json())


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
    image_counter = 0
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
            markdown_content = content or None
            if label == "image":
                markdown_content = None
                image_path = raw_block.get("image_path")
                if image_path is not None:
                    try:
                        path = validated_sdk_image_path(image_path)
                    except ValueError as error:
                        raise DocumentError(
                            "invalid_model_output",
                            str(error),
                            retryable=True,
                        ) from error
                    markdown_content = (
                        f"![Image {page_index}-{image_counter}](../{path.as_posix()})"
                    )
                    image_counter += 1
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
                    markdown_content=markdown_content,
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


def validated_sdk_image_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str):
        raise ValueError("GLM-OCR image_path must be text")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or len(path.parts) != 2
        or path.parts[0] != "imgs"
        or path.name in {"", ".", ".."}
    ):
        raise ValueError(f"Unsafe GLM-OCR image path: {value!r}")
    return path


def write_elements(path: Path, elements: Iterable[OcrElement]) -> None:
    with (
        path.open("xb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        io.TextIOWrapper(compressed, encoding="utf-8") as output,
    ):
        for element in elements:
            output.write(element.model_dump_json())
            output.write("\n")


def copy_sdk_artifacts(
    saved_root: Path,
    destination: Path,
    *,
    expected_pages: int,
) -> None:
    images = saved_root / "imgs"
    if images.is_dir():
        shutil.copytree(images, destination / "imgs")
    visualizations = saved_root / "layout_vis"
    sources = (
        tuple(path for path in visualizations.iterdir() if path.is_file())
        if visualizations.is_dir()
        else ()
    )
    indexed: dict[int, Path] = {}
    if expected_pages == 1 and len(sources) == 1:
        indexed[1] = sources[0]
    else:
        for source in sources:
            match = re.search(r"_page([0-9]+)$", source.stem)
            if match is None:
                raise DocumentError(
                    "invalid_model_output",
                    f"Cannot resolve SDK visualization page from {source.name}",
                    retryable=True,
                )
            page_number = int(match.group(1)) + 1
            if page_number in indexed:
                raise DocumentError(
                    "invalid_model_output",
                    f"Duplicate SDK visualization for page {page_number}",
                    retryable=True,
                )
            indexed[page_number] = source
    expected = set(range(1, expected_pages + 1))
    if set(indexed) != expected:
        raise DocumentError(
            "incomplete_model_output",
            f"GLM-OCR produced visualizations for pages {sorted(indexed)}, "
            f"expected {sorted(expected)}",
            retryable=True,
        )
    target = destination / "layout_vis"
    target.mkdir()
    for page_number, source in sorted(indexed.items()):
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


def prepare_document(
    job: OcrJob,
    request: OcrDocumentRequest,
    work_root: Path,
    *,
    document_count: int,
    document_index: int,
    started_at: float,
) -> PreparedDocument | OcrDocumentResult:
    pdf = work_root / "source.pdf"
    pdf_hash, pdf_size = download_pdf(request, pdf, job.limits.max_pdf_bytes)
    pages = pdf_page_count(pdf, job.limits.max_pages_per_document)
    log_stage(
        "document_loaded",
        started_at,
        arxiv_id=request.arxiv_id,
        document_count=document_count,
        document_index=document_index,
        page_count=pages,
        pdf_size_bytes=pdf_size,
        request_id=request.request_id,
    )
    reuse = request.reuse
    if reuse is not None and reuse.pdf_sha256 == pdf_hash:
        if not reuse.matches(
            pdf_sha256=pdf_hash,
            pdf_size_bytes=pdf_size,
            page_count=pages,
        ):
            raise DocumentError(
                "reuse_lineage_mismatch",
                "Previously imported OCR lineage disagrees with unchanged PDF content",
                retryable=False,
            )
        log_stage(
            "document_reused",
            started_at,
            arxiv_id=request.arxiv_id,
            document_count=document_count,
            document_index=document_index,
            page_count=pages,
            request_id=request.request_id,
        )
        return OcrDocumentResult(
            request_id=request.request_id,
            arxiv_id=request.arxiv_id,
            state="reused",
            pdf_sha256=pdf_hash,
            pdf_size_bytes=pdf_size,
            page_count=pages,
            processing_id=reuse.processing_id,
            manifest_sha256=reuse.manifest_sha256,
        )
    return PreparedDocument(
        request=request,
        work_root=work_root,
        pdf_path=pdf,
        pdf_sha256=pdf_hash,
        pdf_size_bytes=pdf_size,
        page_count=pages,
        document_count=document_count,
        document_index=document_index,
        started_at=started_at,
    )


def successful_result(
    job: OcrJob,
    prepared: PreparedDocument,
    parser: GlmOcr,
    output_root: Path,
) -> OcrDocumentResult:
    request = prepared.request
    pdf = prepared.pdf_path
    pdf_hash = prepared.pdf_sha256
    pdf_size = prepared.pdf_size_bytes
    pages = prepared.page_count
    work_root = prepared.work_root
    current_processing_id = processing_id(
        arxiv_id=request.arxiv_id,
        pdf_sha256=pdf_hash,
        configuration_hash=job.config_hash,
    )
    try:
        with GlmOcrPageProgress(
            parser,
            arxiv_id=request.arxiv_id,
            document_count=prepared.document_count,
            document_index=prepared.document_index,
            page_count=pages,
            request_id=request.request_id,
            started_at=prepared.started_at,
            emit=log_stage,
        ):
            parsed = parser.parse(pdf, save_layout_visualization=True)
    except Exception as error:
        raise DocumentError("ocr_inference_failed", str(error), retryable=True) from error

    document_root = work_root / "committed-output"
    document_root.mkdir()
    layout = normalized_layout(parsed.json_result, pages)
    elements = canonical_elements(layout, current_processing_id)
    page_bundle = build_page_markdown_bundle(elements, page_count=pages)
    write_page_markdown_bundle(
        document_root.joinpath(*PAGE_MARKDOWN_BUNDLE_PATH.parts),
        page_bundle,
    )
    write_elements(document_root / "elements.jsonl.gz", elements)

    saved = work_root / "sdk-output"
    parsed.save(output_dir=saved, save_layout_visualization=True)
    saved_directories = [path for path in saved.iterdir() if path.is_dir()]
    if len(saved_directories) == 1:
        copy_sdk_artifacts(
            saved_directories[0],
            document_root,
            expected_pages=pages,
        )

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
        prepared.started_at,
        arxiv_id=request.arxiv_id,
        document_count=prepared.document_count,
        document_index=prepared.document_index,
        page_count=pages,
        request_id=request.request_id,
    )
    return result


def log_batch_progress(
    job: OcrJob,
    results: dict[str, OcrDocumentResult],
    started_at: float,
) -> None:
    values = tuple(results.values())
    log_stage(
        "batch_progress",
        started_at,
        batch_id=job.batch_id,
        completed_documents=len(values),
        document_count=len(job.documents),
        failed_documents=sum(
            item.state in {"retryable_failed", "terminal_failed"} for item in values
        ),
        reused_documents=sum(item.state == "reused" for item in values),
        succeeded_documents=sum(item.state == "succeeded" for item in values),
    )


def failed_result(request: OcrDocumentRequest, error: DocumentError) -> OcrDocumentResult:
    return OcrDocumentResult(
        request_id=request.request_id,
        arxiv_id=request.arxiv_id,
        state="retryable_failed" if error.retryable else "terminal_failed",
        error_code=error.code,
        error_message=str(error)[:ERROR_MESSAGE_CHARACTERS],
    )


def log_document_failure(
    request: OcrDocumentRequest,
    error: DocumentError,
    *,
    document_count: int,
    document_index: int,
    started_at: float,
) -> None:
    """Emit the durable failure detail without allowing unbounded provider logs."""
    log_stage(
        "document_failed",
        started_at,
        arxiv_id=request.arxiv_id,
        document_count=document_count,
        document_index=document_index,
        error_code=error.code,
        error_message=str(error)[:ERROR_MESSAGE_CHARACTERS],
        request_id=request.request_id,
        retryable=error.retryable,
    )


def write_config(path: Path, job: OcrJob, layout_model_path: str) -> None:
    # Preserve the pinned SDK's complete task/label/prompt configuration and
    # override only settings owned by this job. A minimal YAML silently drops
    # the official table/formula mappings and treats every region as text.
    config = GlmOcrConfig.from_yaml().to_dict()
    pipeline = config["pipeline"]
    pipeline["maas"]["enabled"] = False
    pipeline["max_workers"] = job.inference.max_workers
    pipeline["ocr_api"].update(
        {
            "api_host": "127.0.0.1",
            "api_port": job.inference.api_port,
            "model": "glm-ocr",
            "request_timeout": job.inference.request_timeout_seconds,
        }
    )
    pipeline["page_loader"]["pdf_max_pages"] = job.limits.max_pages_per_document
    pipeline["layout"].update(
        {
            "model_dir": layout_model_path,
            "device": job.inference.layout_device,
        }
    )
    validated = GlmOcrConfig.model_validate(config)
    path.write_text(
        yaml.safe_dump(validated.to_dict(), sort_keys=True),
        encoding="utf-8",
    )


def resolve_model_source(
    source: str,
    *,
    resource_name: str,
    repository: str,
    revision: str,
) -> Path:
    candidate = Path(source)
    if candidate.is_absolute():
        if not candidate.is_dir():
            raise RuntimeError(f"Provisioned OCR model path does not exist: {candidate}")
        path = candidate
    else:
        import kagglehub

        path = Path(kagglehub.model_download(source))
    try:
        manifest = json.loads((path / MODEL_MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"OCR model {source!r} has an invalid resource manifest") from error
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
        raise RuntimeError(f"OCR model {source!r} does not match the OCR job")
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


class InferenceEngine:
    """Reuse one compatible vLLM server and parser across bounded batches."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._signature: str | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._stack: ExitStack | None = None
        self._parser: GlmOcr | None = None
        self._log_path = root / "vllm.log"

    def acquire(
        self,
        job: OcrJob,
        *,
        model_path: Path,
        layout_model_path: Path,
    ) -> tuple[GlmOcr, Path]:
        signature = canonical_json_sha256(
            {
                "inference": job.inference.model_dump(mode="json"),
                "layout_model_path": str(layout_model_path),
                "max_pages_per_document": job.limits.max_pages_per_document,
                "model_path": str(model_path),
            }
        )
        process_failed = self._process is not None and self._process.poll() is not None
        if self._signature != signature or process_failed:
            self.close()
            self._root.mkdir(parents=True, exist_ok=True)
            config_path = self._root / "glmocr.yaml"
            write_config(config_path, job, str(layout_model_path))
            process, log_file = start_vllm(job, model_path, self._log_path)
            stack = ExitStack()
            stack.callback(stop_process, process, log_file)
            try:
                parser = stack.enter_context(
                    GlmOcr(
                        config_path=str(config_path),
                        layout_device=job.inference.layout_device,
                    )
                )
            except Exception:
                stack.close()
                raise
            self._signature = signature
            self._process = process
            self._stack = stack
            self._parser = parser
        if self._parser is None:
            raise RuntimeError("GLM-OCR inference engine did not initialize")
        return self._parser, self._log_path

    def close(self) -> None:
        self._parser = None
        self._process = None
        stack, self._stack = self._stack, None
        self._signature = None
        if stack is not None:
            stack.close()


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
    """Persist bounded server evidence and mirror a useful tail to the provider log."""
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
    engine: InferenceEngine | None = None,
) -> None:
    batch_started_at = time.perf_counter()
    log_stage(
        "batch_started",
        batch_started_at,
        batch_id=job.batch_id,
        document_count=len(job.documents),
        enforce_eager=job.inference.enforce_eager,
        max_num_seqs=job.inference.max_num_seqs,
        max_workers=job.inference.max_workers,
        arxiv_ids=[request.arxiv_id for request in job.documents],
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="glm-ocr-") as raw_temp:
        temp = Path(raw_temp)
        extracted = temp / "result"
        extracted.mkdir()
        document_count = len(job.documents)
        prepared_documents: list[PreparedDocument] = []
        results: dict[str, OcrDocumentResult] = {}
        for document_index, request in enumerate(job.documents, start=1):
            document_started_at = time.perf_counter()
            document_work = temp / "work" / request.request_id
            document_work.mkdir(parents=True)
            log_stage(
                "document_started",
                document_started_at,
                arxiv_id=request.arxiv_id,
                document_count=document_count,
                document_index=document_index,
                request_id=request.request_id,
            )
            try:
                prepared = prepare_document(
                    job,
                    request,
                    document_work,
                    document_count=document_count,
                    document_index=document_index,
                    started_at=document_started_at,
                )
                if isinstance(prepared, OcrDocumentResult):
                    results[request.request_id] = prepared
                else:
                    prepared_documents.append(prepared)
            except DocumentError as error:
                results[request.request_id] = failed_result(request, error)
                log_document_failure(
                    request,
                    error,
                    document_count=document_count,
                    document_index=document_index,
                    started_at=document_started_at,
                )
            except Exception as error:
                document_error = DocumentError(
                    "unexpected_runner_error",
                    str(error),
                    retryable=True,
                )
                results[request.request_id] = failed_result(request, document_error)
                log_document_failure(
                    request,
                    document_error,
                    document_count=document_count,
                    document_index=document_index,
                    started_at=document_started_at,
                )
            if request.request_id in results:
                log_batch_progress(job, results, batch_started_at)

        if prepared_documents:
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
            server_started_at = time.perf_counter()
            inference_engine = engine or InferenceEngine(temp / "engine")
            parser, vllm_log_path = inference_engine.acquire(
                job,
                model_path=model_path,
                layout_model_path=layout_path,
            )
            log_stage("vllm_ready", server_started_at, batch_id=job.batch_id)
            try:
                for prepared in prepared_documents:
                    request = prepared.request
                    try:
                        result = successful_result(
                            job,
                            prepared,
                            parser,
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
                        log_document_failure(
                            request,
                            error,
                            document_count=prepared.document_count,
                            document_index=prepared.document_index,
                            started_at=prepared.started_at,
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
                        log_document_failure(
                            request,
                            document_error,
                            document_count=prepared.document_count,
                            document_index=prepared.document_index,
                            started_at=prepared.started_at,
                        )
                    results[request.request_id] = result
                    log_batch_progress(job, results, batch_started_at)
            except Exception as error:
                persist_vllm_diagnostic(
                    output_directory,
                    vllm_log_path,
                    job,
                    error=error,
                    request=None,
                )
                if engine is not None:
                    engine.close()
                raise
            finally:
                if engine is None:
                    inference_engine.close()
        else:
            log_stage(
                "inference_skipped",
                batch_started_at,
                batch_id=job.batch_id,
                reused_documents=sum(item.state == "reused" for item in results.values()),
            )

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
            documents=tuple(results[request.request_id] for request in job.documents),
        )
        write_text_atomic(
            manifest_path,
            manifest.model_dump_json(indent=2),
        )
        log_stage(
            "batch_committed",
            batch_started_at,
            batch_id=job.batch_id,
            document_count=len(job.documents),
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
