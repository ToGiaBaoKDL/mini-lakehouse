"""Validated configuration shared by the Modal client and runtime."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field

from document_ocr.identity import canonical_json_sha256, job_id
from document_ocr.protocol import OCR_PROTOCOL_VERSION, OcrJob, OcrLimits, OcrReuseReference


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelConfig(ConfigModel):
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")


class PromptConfig(ConfigModel):
    text: str = Field(min_length=1, max_length=255)
    table: str = Field(min_length=1, max_length=255)
    formula: str = Field(min_length=1, max_length=255)


class InferenceConfig(ConfigModel):
    api_port: int = Field(ge=1024, le=65535)
    max_model_len: int = Field(ge=4096, le=131072)
    gpu_memory_utilization: float = Field(gt=0, le=0.9)
    speculative_tokens: int = Field(ge=0, le=8)
    max_num_seqs: int = Field(ge=1, le=32)
    max_workers: int = Field(ge=1, le=32)
    request_timeout_seconds: int = Field(ge=30, le=3600)
    layout_device: Literal["cpu", "cuda:0"]
    prompts: PromptConfig


class ModalConfig(ConfigModel):
    app_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
    output_volume: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
    environment: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    gpu: Literal["A100", "A100-40GB", "A100-80GB"]
    timeout_seconds: int = Field(ge=300, le=24 * 60 * 60)
    scaledown_window_seconds: int = Field(ge=0, le=20 * 60)


class OcrConfig(ConfigModel):
    sdk_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    adapter_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    limits: OcrLimits
    model: ModelConfig
    layout_model: ModelConfig
    inference: InferenceConfig
    modal: ModalConfig

    @property
    def configuration_hash(self) -> str:
        """Hash output semantics while excluding Modal delivery tuning."""
        return canonical_json_sha256(
            {
                "adapter_version": self.adapter_version,
                "inference": self.inference.model_dump(mode="json"),
                "layout_model": self.layout_model.model_dump(mode="json"),
                "model": self.model.model_dump(mode="json"),
                "protocol_version": OCR_PROTOCOL_VERSION,
                "sdk_version": self.sdk_version,
            }
        )

    def job(
        self,
        *,
        document_id: str,
        source_record_sha256: str,
        pdf_url: str,
        reuse: OcrReuseReference | None,
    ) -> OcrJob:
        return OcrJob(
            job_id=job_id(
                document_id=document_id,
                source_record_sha256=source_record_sha256,
                configuration_hash=self.configuration_hash,
            ),
            config_hash=self.configuration_hash,
            limits=self.limits,
            document_id=document_id,
            source_record_sha256=source_record_sha256,
            pdf_url=pdf_url,
            reuse=reuse,
        )


@lru_cache(maxsize=2)
def load_config(path: Path = Path("ocr-engine/config.yaml")) -> OcrConfig:
    resolved = path.resolve()
    try:
        with resolved.open(encoding="utf-8") as stream:
            payload = yaml.safe_load(stream)
    except OSError as error:
        raise ValueError(f"Cannot read OCR config {resolved}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"OCR config {resolved} must contain a YAML object")
    try:
        return OcrConfig.model_validate(cast(dict[str, object], payload))
    except ValueError as error:
        raise ValueError(f"Invalid OCR config {resolved}: {error}") from error
