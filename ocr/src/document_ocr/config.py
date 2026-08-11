from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, cast, overload

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from document_ocr.identity import canonical_json_sha256, run_id
from document_ocr.protocol import (
    OCR_PROTOCOL_VERSION,
    DocumentJob,
    OcrDocumentRequest,
    OcrInference,
    OcrJob,
    OcrLimits,
    OcrModel,
    OpenDataLoaderJob,
    OpenDataLoaderOptions,
)

type ConfigName = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]
type ExecutionBackendName = Literal["kaggle", "modal", "oci"]


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
    environment: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    gpu: Literal["A100", "A100-40GB", "A100-80GB"]
    timeout_seconds: int = Field(ge=300, le=24 * 60 * 60)
    scaledown_window_seconds: int = Field(ge=0, le=20 * 60)


class ProcessorRunnerConfig(ConfigModel):
    kaggle: KaggleRunnerConfig
    modal: ModalRunnerConfig


class ProcessorConfigBase(ConfigModel):
    version: Literal[1]
    name: ConfigName
    adapter_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    limits: OcrLimits

    @property
    def semantic_configuration(self) -> dict[str, object]:
        raise NotImplementedError

    @property
    def configuration_hash(self) -> str:
        """Hash output semantics while excluding execution and delivery tuning."""
        return canonical_json_sha256(
            {
                **self.semantic_configuration,
                "adapter_version": self.adapter_version,
                "protocol_version": OCR_PROTOCOL_VERSION,
            }
        )

    def build_job(self, document: OcrDocumentRequest, *, attempt: int) -> DocumentJob:
        raise NotImplementedError


class GlmOcrConfig(ProcessorConfigBase):
    adapter: Literal["glm_ocr"]
    model: OcrModel
    layout_model: OcrModel
    inference: OcrInference
    runner: ProcessorRunnerConfig

    @property
    def semantic_configuration(self) -> dict[str, object]:
        return {
            "adapter": self.adapter,
            "inference": {
                "dtype": self.inference.dtype,
                "layout_device": self.inference.layout_device,
                "max_model_len": self.inference.max_model_len,
                "speculative_tokens": self.inference.speculative_tokens,
            },
            "layout_model": self.layout_model.model_dump(mode="json"),
            "model": self.model.model_dump(mode="json"),
        }

    def build_job(self, document: OcrDocumentRequest, *, attempt: int) -> OcrJob:
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


class OpenDataLoaderConfig(ProcessorConfigBase):
    adapter: Literal["opendataloader_pdf"]
    options: OpenDataLoaderOptions

    @property
    def semantic_configuration(self) -> dict[str, object]:
        return {
            "adapter": self.adapter,
            "options": self.options.model_dump(mode="json"),
        }

    def build_job(self, document: OcrDocumentRequest, *, attempt: int) -> OpenDataLoaderJob:
        return OpenDataLoaderJob(
            run_id=run_id(document.request_id, attempt),
            attempt=attempt,
            adapter=self.adapter,
            adapter_version=self.adapter_version,
            config_hash=self.configuration_hash,
            limits=self.limits,
            options=self.options,
            document=document,
        )


type OcrConfig = Annotated[
    GlmOcrConfig | OpenDataLoaderConfig,
    Field(discriminator="adapter"),
]
OCR_CONFIG_ADAPTER = TypeAdapter(OcrConfig)


class PipelineConfig(ConfigModel):
    processor: ConfigName
    execution_backend: ExecutionBackendName


class PipelineRegistry(ConfigModel):
    version: Literal[1]
    default_pipeline: ConfigName
    pipelines: dict[ConfigName, PipelineConfig] = Field(min_length=1)

    def pipeline(self, name: str) -> PipelineConfig:
        try:
            return self.pipelines[name]
        except KeyError as error:
            supported = ", ".join(sorted(self.pipelines))
            raise ValueError(
                f"Unsupported OCR pipeline {name!r}; expected one of: {supported}"
            ) from error


def _yaml_object(path: Path) -> dict[str, object]:
    try:
        with path.open(encoding="utf-8") as stream:
            payload = yaml.safe_load(stream)
    except OSError as error:
        raise ValueError(f"Cannot read OCR config {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"OCR config {path} must contain a YAML object")
    return cast(dict[str, object], payload)


@overload
def load_ocr_config(
    name: Literal["arxiv_glm_ocr"],
    root: Path = Path("ocr/config"),
) -> GlmOcrConfig: ...


@overload
def load_ocr_config(
    name: Literal["arxiv_opendataloader_pdf"],
    root: Path = Path("ocr/config"),
) -> OpenDataLoaderConfig: ...


@overload
def load_ocr_config(
    name: str,
    root: Path = Path("ocr/config"),
) -> OcrConfig: ...


@lru_cache(maxsize=8)
def load_ocr_config(
    name: str,
    root: Path = Path("ocr/config"),
) -> OcrConfig:
    path = (root / f"{name}.yaml").resolve()
    if not path.is_file():
        raise ValueError(f"Processor config does not exist: {path}")
    try:
        return OCR_CONFIG_ADAPTER.validate_python(_yaml_object(path))
    except ValueError as error:
        raise ValueError(f"Invalid OCR config {path}: {error}") from error


@lru_cache(maxsize=2)
def load_pipeline_registry(root: Path = Path("ocr/config")) -> PipelineRegistry:
    path = (root / "arxiv_pipelines.yaml").resolve()
    try:
        registry = PipelineRegistry.model_validate(_yaml_object(path))
        if registry.default_pipeline not in registry.pipelines:
            raise ValueError("default_pipeline must reference a configured pipeline")
        for name, pipeline in registry.pipelines.items():
            processor = load_ocr_config(pipeline.processor, root)
            if pipeline.execution_backend == "oci" and not isinstance(
                processor, OpenDataLoaderConfig
            ):
                raise ValueError(f"Pipeline {name!r} requires an OCI-native processor")
            if pipeline.execution_backend != "oci" and not isinstance(processor, GlmOcrConfig):
                raise ValueError(f"Pipeline {name!r} requires the GLM-OCR processor")
        return registry
    except ValueError as error:
        raise ValueError(f"Invalid OCR pipeline registry {path}: {error}") from error
