"""Compose the ArXiv OCR workflow from AWS-managed runtime resources."""

import json
from typing import cast

import boto3
from lakehouse.aws import get_runtime_parameter
from lakehouse.config.settings import get_settings
from lakehouse.contracts import load_contracts
from lakehouse.iceberg import load_iceberg_catalog
from pyiceberg.table import Table

from document_ocr import OcrConfig, OcrProvider, OcrProviderName, load_ocr_config
from document_ocr.arxiv.store import ArxivOcrStore
from document_ocr.arxiv.workflow import ArxivOcrWorkflow
from document_ocr.providers.kaggle import KaggleProvider
from document_ocr.providers.modal import ModalProvider
from document_ocr.settings import KaggleSettings, ModalSettings


def _provider_credentials(name: OcrProviderName) -> dict[str, object]:
    settings = get_settings()
    secret_id = get_runtime_parameter(
        settings.environment,
        f"ocr/providers/{name}_secret_id",
    )
    response = boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)
    secret = response.get("SecretString")
    if not isinstance(secret, str):
        raise RuntimeError(f"OCR provider secret {secret_id!r} has no SecretString")
    payload = json.loads(secret)
    if not isinstance(payload, dict):
        raise RuntimeError(f"OCR provider secret {secret_id!r} must contain a JSON object")
    return cast(dict[str, object], payload)


def _provider(name: OcrProviderName, processor: OcrConfig) -> OcrProvider:
    credentials = _provider_credentials(name)
    if name == "kaggle":
        return KaggleProvider(KaggleSettings.model_validate(credentials), processor)
    return ModalProvider(ModalSettings.model_validate(credentials), processor)


def run_arxiv_ocr(arxiv_id: str, provider_name: str) -> dict[str, object]:
    """Run and publish OCR for exactly one curated ArXiv document."""
    document_id = arxiv_id.strip()
    if not document_id:
        raise ValueError("arxiv_id must not be empty")
    if provider_name not in {"kaggle", "modal"}:
        raise ValueError(f"Unsupported OCR provider {provider_name!r}")

    settings = get_settings()
    processor = load_ocr_config("arxiv_glm_ocr")
    provider = _provider(cast(OcrProviderName, provider_name), processor)
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
    result = ArxivOcrWorkflow(
        store=store,
        processor=processor,
        provider=provider,
    ).run(document_id)
    return {
        "arxiv_id": document_id,
        "run_id": result["run_id"],
        "state": result["state"],
    }
