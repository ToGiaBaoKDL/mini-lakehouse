"""Start the self-hosted endpoint consumed by the official GLM-OCR SDK."""

import json
import os
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO

import requests
import yaml
from document_ocr.config import OcrConfig
from glmocr import GlmOcr
from glmocr.config import GlmOcrConfig

STARTUP_TIMEOUT_SECONDS = 15 * 60


def _sdk_config(path: Path, config: OcrConfig, layout_model_path: Path) -> None:
    value = GlmOcrConfig.from_yaml().to_dict()
    pipeline = value["pipeline"]
    pipeline["maas"]["enabled"] = False
    pipeline["max_workers"] = config.inference.max_workers
    pipeline["ocr_api"].update(
        {
            "api_host": "127.0.0.1",
            "api_port": config.inference.api_port,
            "model": "glm-ocr",
            "request_timeout": config.inference.request_timeout_seconds,
        }
    )
    pipeline["page_loader"].update(
        {
            "pdf_max_pages": config.limits.max_pages_per_document,
            "task_prompt_mapping": config.inference.prompts.model_dump(mode="json"),
        }
    )
    pipeline["layout"].update(
        {
            "model_dir": str(layout_model_path),
            "device": config.inference.layout_device,
        }
    )
    validated = GlmOcrConfig.model_validate(value)
    path.write_text(yaml.safe_dump(validated.to_dict(), sort_keys=True), encoding="utf-8")


def _command(config: OcrConfig, model_path: Path) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        str(model_path),
        "--served-model-name",
        "glm-ocr",
        "--port",
        str(config.inference.api_port),
        "--dtype",
        "float16",
        "--max-model-len",
        str(config.inference.max_model_len),
        "--gpu-memory-utilization",
        str(config.inference.gpu_memory_utilization),
        "--max-num-seqs",
        str(config.inference.max_num_seqs),
        "--enforce-eager",
    ]
    if config.inference.speculative_tokens:
        command.extend(
            [
                "--speculative-config",
                json.dumps(
                    {
                        "method": "mtp",
                        "num_speculative_tokens": config.inference.speculative_tokens,
                    },
                    separators=(",", ":"),
                ),
            ]
        )
    return command


def start(
    config: OcrConfig,
    *,
    model_path: Path,
    layout_model_path: Path,
    root: Path,
) -> tuple[subprocess.Popen[bytes], BinaryIO, GlmOcr]:
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "vllm.log"
    log_file = log_path.open("wb")
    process = subprocess.Popen(
        _command(config, model_path),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env={**os.environ, "VLLM_USE_FLASHINFER_SAMPLER": "0"},
    )
    endpoint = f"http://127.0.0.1:{config.inference.api_port}/v1/models"
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            diagnostic = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
            log_file.close()
            raise RuntimeError(
                f"vLLM exited with status {process.returncode}; log tail: {diagnostic}"
            )
        try:
            if requests.get(endpoint, timeout=5).ok:
                sdk_config = root / "glmocr.yaml"
                _sdk_config(sdk_config, config, layout_model_path)
                parser = GlmOcr(
                    config_path=str(sdk_config),
                    mode="selfhosted",
                    layout_device=config.inference.layout_device,
                )
                return process, log_file, parser
        except requests.RequestException:
            pass
        time.sleep(5)
    stop(process, log_file)
    raise RuntimeError("vLLM did not become ready within 15 minutes")


def stop(process: subprocess.Popen[bytes], log_file: BinaryIO) -> None:
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
