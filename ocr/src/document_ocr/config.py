from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

type ConfigName = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProcessorModelConfig(ConfigModel):
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")


class ProcessorBatchConfig(ConfigModel):
    max_documents: int = Field(ge=1, le=10)
    max_pdf_bytes: int = Field(ge=1024 * 1024)
    max_pages_per_document: int = Field(ge=1, le=2000)
    max_output_bytes: int = Field(ge=1024 * 1024)


class ProcessorInferenceConfig(ConfigModel):
    api_port: int = Field(ge=1024, le=65535)
    dtype: Literal["float16"]
    max_model_len: int = Field(ge=4096, le=131072)
    gpu_memory_utilization: float = Field(gt=0, le=0.9)
    speculative_tokens: int = Field(ge=0, le=8)
    enforce_eager: bool
    max_num_seqs: int = Field(ge=1, le=32)
    max_workers: int = Field(ge=1, le=32)
    request_timeout_seconds: int = Field(ge=30, le=3600)
    layout_device: Literal["cpu", "cuda:0"]


class KaggleRunnerConfig(ConfigModel):
    kernel_name: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    runner_dataset_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,49}$")
    runner_dataset_version: int = Field(ge=1)
    model_source: str = Field(min_length=1)
    layout_model_source: str = Field(min_length=1)
    accelerator: Literal["NvidiaTeslaP100", "NvidiaTeslaT4"]
    timeout_seconds: int = Field(ge=300, le=12 * 60 * 60)


class ModalRunnerConfig(ConfigModel):
    app_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
    function_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    output_volume: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
    environment: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")
    gpu: Literal["A100", "A100-40GB", "A100-80GB"]
    timeout_seconds: int = Field(ge=300, le=24 * 60 * 60)
    scaledown_window_seconds: int = Field(ge=0, le=20 * 60)


class ProcessorRunnerConfig(ConfigModel):
    default_provider: Literal["kaggle", "modal"]
    kaggle: KaggleRunnerConfig
    modal: ModalRunnerConfig


class OcrConfig(ConfigModel):
    version: Literal[1]
    name: ConfigName
    adapter: Literal["glm_ocr"]
    adapter_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    output_schema_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    model: ProcessorModelConfig
    layout_model: ProcessorModelConfig
    batch: ProcessorBatchConfig
    inference: ProcessorInferenceConfig
    runner: ProcessorRunnerConfig


@lru_cache(maxsize=8)
def load_ocr_config(
    name: str,
    root: Path = Path("ocr/config"),
) -> OcrConfig:
    path = (root / f"{name}.yaml").resolve()
    if not path.is_file():
        raise ValueError(f"Processor config does not exist: {path}")
    try:
        with path.open(encoding="utf-8") as stream:
            payload = yaml.safe_load(stream)
        return OcrConfig.model_validate(payload)
    except ValueError as error:
        raise ValueError(f"Invalid OCR config {path}: {error}") from error
