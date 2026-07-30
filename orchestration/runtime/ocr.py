"""Runtime composition for document OCR workflows."""

from typing import cast

import boto3
from airflow.sdk.bases.hook import BaseHook
from document_ocr import OcrConfig, OcrProvider, OcrProviderName, load_ocr_config
from document_ocr.application import ArxivOcrPipeline, ArxivOcrStore
from document_ocr.providers.kaggle import KaggleProvider
from document_ocr.providers.modal import ModalProvider
from document_ocr.settings import KaggleSettings, ModalSettings
from pyiceberg.table import Table

from lakehouse_platform.aws import get_runtime_parameter
from lakehouse_platform.config.settings import get_settings
from lakehouse_platform.contracts import load_contracts
from lakehouse_platform.platform.iceberg import load_iceberg_catalog


def _provider(
    name: OcrProviderName,
    connection_id: str,
    processor: OcrConfig,
) -> OcrProvider:
    connection = BaseHook.get_connection(connection_id)
    if name == "kaggle":
        return KaggleProvider(
            KaggleSettings.model_validate(
                {"username": connection.login, "api_token": connection.password}
            ),
            processor,
        )
    return ModalProvider(
        ModalSettings.model_validate(
            {"token_id": connection.login, "token_secret": connection.password}
        ),
        processor,
    )


def run_arxiv_ocr(arxiv_id: str, provider_name: str) -> dict[str, object]:
    """Resolve runtime resources and OCR exactly one curated ArXiv paper."""
    document_id = arxiv_id.strip()
    if not document_id:
        raise ValueError("Trigger this DAG with exactly one arxiv_id")
    if provider_name not in {"kaggle", "modal"}:
        raise ValueError(f"Unsupported OCR provider {provider_name!r}")
    provider_key = cast(OcrProviderName, provider_name)
    processor = load_ocr_config("arxiv_glm_ocr")
    provider = _provider(provider_key, f"{provider_key}_default", processor)

    settings = get_settings()
    curated_uri = get_runtime_parameter(settings.environment, "storage/curated_uri")
    catalog = load_iceberg_catalog(
        get_runtime_parameter(settings.environment, "catalog/name"),
        region_name=settings.aws_region,
        profile_name=settings.aws_profile,
    )
    product = load_contracts(settings.contracts_dir).curated_product("arxiv")

    def table(key: str) -> Table:
        return catalog.load_table(product.table_identifier(key).iceberg)

    store = ArxivOcrStore(
        papers=table("papers"),
        runs=table("ocr_document_runs"),
        elements=table("ocr_document_elements"),
        curated_uri=curated_uri,
        product_name=product.name,
        s3_client=boto3.Session(
            region_name=settings.aws_region,
            profile_name=settings.aws_profile,
        ).client("s3"),
    )
    result = ArxivOcrPipeline(
        store=store,
        processor=processor,
        provider=provider,
    ).run(document_id)
    return {
        "arxiv_id": document_id,
        "run_id": result["run_id"],
        "state": result["state"],
    }
