"""Compose the ArXiv OCR workflow from AWS-managed runtime resources."""

import json
import signal
from typing import cast

import boto3
from lakehouse.aws import get_runtime_parameter
from lakehouse.config.settings import get_settings
from lakehouse.contracts import load_contracts
from lakehouse.iceberg import load_iceberg_catalog
from loguru import logger
from pyiceberg.table import Table

from document_ocr import (
    OcrProvider,
    OcrProviderError,
    load_ocr_config,
)
from document_ocr.arxiv.store import ArxivOcrStore
from document_ocr.arxiv.workflow import ArxivOcrWorkflow
from document_ocr.config import GlmOcrConfig, OpenDataLoaderConfig, load_pipeline_registry
from document_ocr.execution import OciExecutionBackend, RemoteExecutionBackend
from document_ocr.providers.modal import ModalProvider
from document_ocr.settings import ModalSettings


def _modal_credentials() -> dict[str, object]:
    settings = get_settings()
    secret_id = get_runtime_parameter(
        settings.environment,
        "ocr/providers/modal_secret_id",
    )
    response = boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)
    secret = response.get("SecretString")
    if not isinstance(secret, str):
        raise RuntimeError(f"OCR provider secret {secret_id!r} has no SecretString")
    payload = json.loads(secret)
    if not isinstance(payload, dict):
        raise RuntimeError(f"OCR provider secret {secret_id!r} must contain a JSON object")
    return cast(dict[str, object], payload)


def _provider(processor: GlmOcrConfig) -> OcrProvider:
    return ModalProvider(ModalSettings.model_validate(_modal_credentials()), processor)


def run_arxiv_ocr(arxiv_id: str, pipeline_name: str | None = None) -> dict[str, object]:
    """Run and publish one configured extraction pipeline for one ArXiv PDF."""
    document_id = arxiv_id.strip()
    if not document_id:
        raise ValueError("arxiv_id must not be empty")
    settings = get_settings()
    registry = load_pipeline_registry()
    selected_pipeline = registry.default_pipeline if pipeline_name is None else pipeline_name
    pipeline = registry.pipeline(selected_pipeline)
    processor = load_ocr_config(pipeline.processor)
    provider: OcrProvider | None = None
    if pipeline.execution_backend == "oci":
        if not isinstance(processor, OpenDataLoaderConfig):
            raise RuntimeError("OCI pipeline processor configuration has drifted")
        execution = OciExecutionBackend()
    else:
        if not isinstance(processor, GlmOcrConfig):
            raise RuntimeError("Remote pipeline processor configuration has drifted")
        provider = _provider(processor)
        execution = RemoteExecutionBackend(provider)
    curated_uri = get_runtime_parameter(settings.environment, "storage/curated_uri")
    catalog = load_iceberg_catalog(region_name=settings.aws_region)
    product = load_contracts(settings.contracts_dir).curated_product("arxiv")

    def table(key: str) -> Table:
        return catalog.load_table(product.table_identifier(key).iceberg)

    store = ArxivOcrStore(
        papers=table("papers"),
        runs=table("ocr_document_runs"),
        elements=table("ocr_document_elements"),
        curated_uri=curated_uri,
        product_name=product.name,
        s3_client=boto3.client("s3"),
    )
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def terminate(signum: int, _frame: object) -> None:
        if isinstance(provider, ModalProvider):
            try:
                provider.cancel()
                logger.warning("Cancelled the active Modal function call")
            except OcrProviderError as error:
                logger.error("Unable to cancel the active Modal function call: {}", error)
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    try:
        result = ArxivOcrWorkflow(
            store=store,
            processor=processor,
            execution=execution,
        ).run(document_id)
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
    return {
        "arxiv_id": document_id,
        "pipeline": selected_pipeline,
        "run_id": result["run_id"],
        "state": result["state"],
    }
