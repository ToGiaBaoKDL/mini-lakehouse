from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from document_ocr.identity import canonical_json_sha256, run_id
from document_ocr.protocol import (
    OCR_PROTOCOL_VERSION,
    OcrDocumentRequest,
    OcrInference,
    OcrJob,
    OcrLimits,
    OcrModel,
)

type ConfigName = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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
    kaggle: KaggleRunnerConfig
    modal: ModalRunnerConfig


class OcrConfig(ConfigModel):
    version: Literal[1]
    name: ConfigName
    adapter: Literal["glm_ocr"]
    adapter_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    model: OcrModel
    layout_model: OcrModel
    limits: OcrLimits
    inference: OcrInference
    runner: ProcessorRunnerConfig

    @property
    def configuration_hash(self) -> str:
        """Hash output semantics while excluding execution and delivery tuning."""
        return canonical_json_sha256(
            {
                "adapter": self.adapter,
                "adapter_version": self.adapter_version,
                "inference": {
                    "dtype": self.inference.dtype,
                    "layout_device": self.inference.layout_device,
                    "max_model_len": self.inference.max_model_len,
                    "speculative_tokens": self.inference.speculative_tokens,
                },
                "layout_model": self.layout_model.model_dump(mode="json"),
                "model": self.model.model_dump(mode="json"),
                "protocol_version": OCR_PROTOCOL_VERSION,
            }
        )

    def build_job(
        self,
        document: OcrDocumentRequest,
        *,
        attempt: int,
    ) -> OcrJob:
        return OcrJob(
            run_id=run_id(document.request_id, attempt),
            attempt=attempt,
            model=self.model,
            layout_model=self.layout_model,
            adapter_version=self.adapter_version,
            config_hash=self.configuration_hash,
            limits=self.limits,
            inference=self.inference,
            document=document,
        )


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
