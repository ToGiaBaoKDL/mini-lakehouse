"""Execute one bounded GLM-OCR batch and emit the canonical output protocol."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
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
from urllib.parse import quote

import fitz
import kagglehub
import requests
import yaml
import zstandard
from glmocr import GlmOcr
from identity import (
    canonical_json_sha256,
    processing_id,
    successful_document_manifest_sha256,
)
from protocol import (
    ArtifactFile,
    OcrBatchManifest,
    OcrDocumentRequest,
    OcrDocumentResult,
    OcrElement,
    OcrJob,
)

SOURCE_DIRECTORY = Path(__file__).resolve().parent
MODEL_MANIFEST_NAME = "mini_lakehouse_resource.json"
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
                sha256=sha256_file(path),
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

    document_root = output_root / "documents" / quote(request.arxiv_id, safe="")
    document_root = document_root / request.request_id
    document_root.mkdir(parents=True)
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
    return OcrDocumentResult.model_validate(payload)


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
    ]
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
            log_file.close()
            raise RuntimeError(f"vLLM exited with status {process.returncode}")
        try:
            if requests.get(endpoint, timeout=5).ok:
                return process, log_file
        except requests.RequestException:
            pass
        time.sleep(5)
    process.terminate()
    log_file.close()
    raise RuntimeError("vLLM did not become ready within 15 minutes")


def create_archive(source: Path, destination: Path) -> None:
    with (
        destination.open("xb") as raw_output,
        zstandard.ZstdCompressor(level=9, write_checksum=True).stream_writer(raw_output) as output,
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


def run(
    job: OcrJob,
    output_directory: Path,
    *,
    model_source: str,
    layout_model_source: str,
) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="glm-ocr-") as raw_temp:
        temp = Path(raw_temp)
        extracted = temp / "result"
        extracted.mkdir()
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
        config_path = temp / "glmocr.yaml"
        write_config(config_path, job, str(layout_path))
        vllm, log_file = start_vllm(job, model_path, temp / "vllm.log")
        results: list[OcrDocumentResult] = []
        try:
            with GlmOcr(
                config_path=str(config_path), layout_device=job.inference.layout_device
            ) as parser:
                for request in job.documents:
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
                        result = failed_result(request, error)
                    except Exception as error:
                        result = failed_result(
                            request,
                            DocumentError("unexpected_runner_error", str(error), retryable=True),
                        )
                    results.append(result)
        finally:
            vllm.terminate()
            with suppress(subprocess.TimeoutExpired):
                vllm.wait(timeout=30)
            if vllm.poll() is None:
                vllm.kill()
            log_file.close()

        archive = output_directory / "result.tar.zst"
        create_archive(extracted, archive)
        manifest = OcrBatchManifest(
            schema_version=job.schema_version,
            batch_id=job.batch_id,
            created_at=datetime.now(UTC),
            archive_sha256=sha256_file(archive),
            archive_size_bytes=archive.stat().st_size,
            documents=tuple(results),
        )
        (output_directory / "result_manifest.json").write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
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
