"""Lifecycle of the local vLLM service and the official GLM-OCR SDK."""

import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import BinaryIO

import requests
import yaml
from document_ocr.identity import canonical_json_sha256
from document_ocr.protocol import OcrJob
from glmocr import GlmOcr
from glmocr.config import GlmOcrConfig

DIAGNOSTIC_CHARACTERS = 32_000
DIAGNOSTIC_MAX_CHARACTERS = 8_000
STARTUP_TIMEOUT_SECONDS = 15 * 60


def write_config(path: Path, job: OcrJob, layout_model_path: Path) -> None:
    """Keep the SDK defaults and override only settings owned by the job."""
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
    pipeline["page_loader"]["task_prompt_mapping"] = job.inference.task_prompts.model_dump(
        mode="json"
    )
    pipeline["layout"].update(
        {
            "model_dir": str(layout_model_path),
            "device": job.inference.layout_device,
        }
    )
    validated = GlmOcrConfig.model_validate(config)
    path.write_text(
        yaml.safe_dump(validated.to_dict(), sort_keys=True),
        encoding="utf-8",
    )


def _vllm_command(job: OcrJob, model_path: Path) -> list[str]:
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
    return command


def start_vllm(
    job: OcrJob,
    model_path: Path,
    log_path: Path,
) -> tuple[subprocess.Popen[bytes], BinaryIO]:
    log_file = log_path.open("wb")
    process = subprocess.Popen(
        _vllm_command(job, model_path),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env={**os.environ, "VLLM_USE_FLASHINFER_SAMPLER": "0"},
    )
    endpoint = f"http://127.0.0.1:{job.inference.api_port}/v1/models"
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")[
                -DIAGNOSTIC_CHARACTERS:
            ]
            failures = [
                line
                for line in log_tail.splitlines()
                if any(
                    marker in line
                    for marker in (" ERROR ", "Error:", "Exception", "failed", "Traceback")
                )
            ]
            diagnostic = (
                "\n".join(failures)[-DIAGNOSTIC_MAX_CHARACTERS:]
                or log_tail[-DIAGNOSTIC_MAX_CHARACTERS:]
            )
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


def stop_process(process: subprocess.Popen[bytes], log_file: BinaryIO) -> None:
    """Stop and reap the local model server within a bounded interval."""
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
    """Reuse one compatible vLLM server and GLM-OCR parser across warm runs."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._signature: str | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._stack: ExitStack | None = None
        self._parser: GlmOcr | None = None

    def acquire(
        self,
        job: OcrJob,
        *,
        model_path: Path,
        layout_model_path: Path,
    ) -> GlmOcr:
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
            write_config(config_path, job, layout_model_path)
            process, log_file = start_vllm(job, model_path, self._root / "vllm.log")
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
        return self._parser

    def close(self) -> None:
        self._parser = None
        self._process = None
        stack, self._stack = self._stack, None
        self._signature = None
        if stack is not None:
            stack.close()
