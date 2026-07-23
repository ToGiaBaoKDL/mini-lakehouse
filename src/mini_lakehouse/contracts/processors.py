from typing import Literal

from pydantic import Field, model_validator

from mini_lakehouse.contracts.base import (
    ContactContract,
    ContractModel,
    ContractName,
    validate_relative_prefix,
)


class ProcessorModelContract(ContractModel):
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")


class ProcessorBatchContract(ContractModel):
    max_documents: int = Field(ge=1, le=50)
    max_pdf_bytes: int = Field(ge=1024 * 1024)
    max_pages_per_document: int = Field(ge=1, le=2000)
    max_output_bytes: int = Field(ge=1024 * 1024)


class ProcessorInferenceContract(ContractModel):
    api_port: int = Field(ge=1024, le=65535)
    dtype: Literal["half"]
    max_model_len: int = Field(ge=4096, le=131072)
    gpu_memory_utilization: float = Field(gt=0, le=0.9)
    speculative_tokens: int = Field(ge=0, le=8)
    max_workers: int = Field(ge=1, le=32)
    layout_device: Literal["cpu", "cuda:0"]


class KaggleRunnerContract(ContractModel):
    provider: Literal["kaggle"]
    kernel_name: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    runner_dataset_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,49}$")
    accelerator: Literal["NvidiaTeslaP100", "NvidiaTeslaT4"]
    timeout_seconds: int = Field(ge=300, le=12 * 60 * 60)
    minimum_gpu_quota_minutes: int = Field(ge=1, le=12 * 60)


class ProcessorRetryContract(ContractModel):
    max_document_attempts: int = Field(ge=1, le=10)


class ProcessorContract(ContractModel):
    version: Literal[1]
    name: ContractName
    owner: ContractName
    contact: ContactContract
    description: str = Field(min_length=1)
    source: ContractName
    curated_product: ContractName
    adapter: Literal["glm_ocr"]
    adapter_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    output_schema_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    artifact_prefix: str
    model: ProcessorModelContract
    layout_model: ProcessorModelContract
    batch: ProcessorBatchContract
    inference: ProcessorInferenceContract
    runner: KaggleRunnerContract
    retry: ProcessorRetryContract

    @model_validator(mode="after")
    def validate_processor(self) -> "ProcessorContract":
        validate_relative_prefix(self.artifact_prefix)
        expected_prefix = f"{self.curated_product}/ocr/"
        if not self.artifact_prefix.startswith(expected_prefix):
            raise ValueError(f"Processor artifacts must live below {expected_prefix!r}")
        return self
