"""Run and publish one ArXiv document from the local CLI."""

import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

import boto3
from lakehouse.aws import get_runtime_parameter
from lakehouse.config.settings import get_settings
from lakehouse.iceberg import load_iceberg_catalog
from pyiceberg.table import Table

from document_ocr.arxiv.store import ArxivOcrStore
from document_ocr.config import OcrConfig, load_config
from document_ocr.modal import ModalOcr
from document_ocr.output import extract_ocr_output
from document_ocr.protocol import OcrError, OcrReuseReference
from lakehouse.contracts import load_contracts


def _modal_credentials() -> dict[str, object]:
    settings = get_settings()
    secret_id = f"lakehouse/{settings.environment}/ocr/providers/modal"
    response = boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)
    secret = response.get("SecretString")
    if not isinstance(secret, str):
        raise RuntimeError(f"OCR provider secret {secret_id!r} has no SecretString")
    payload = json.loads(secret)
    if not isinstance(payload, dict):
        raise RuntimeError(f"OCR provider secret {secret_id!r} must contain a JSON object")
    return cast(dict[str, object], payload)


def process_arxiv_document(
    document_id: str,
    *,
    store: ArxivOcrStore,
    config: OcrConfig,
    modal: ModalOcr,
) -> dict[str, object]:
    """Run and publish one document with dependencies supplied by the CLI boundary."""
    try:
        paper = store.paper(document_id)
    except ValueError as error:
        raise OcrError(str(error)) from error
    if paper["is_deleted"]:
        raise OcrError(f"ArXiv paper {document_id!r} is deleted")

    existing = store.document(document_id)
    if (
        existing is not None
        and existing["source_record_sha256"] == paper["source_record_sha256"]
        and existing["config_hash"] == config.configuration_hash
    ):
        return {
            "arxiv_id": document_id,
            "artifact_uri": existing["artifact_uri"],
            "processing_id": existing["processing_id"],
        }

    reuse = None
    if existing is not None and existing["config_hash"] == config.configuration_hash:
        reuse = OcrReuseReference(
            pdf_sha256=existing["pdf_sha256"],
            pdf_size_bytes=existing["pdf_size_bytes"],
            page_count=existing["page_count"],
            processing_id=existing["processing_id"],
            manifest_sha256=existing["manifest_sha256"],
        )
    job = config.job(
        document_id=document_id,
        source_record_sha256=paper["source_record_sha256"],
        pdf_url=paper["pdf_url"],
        reuse=reuse,
    )

    with TemporaryDirectory(prefix="arxiv-ocr-") as temporary_directory:
        temporary = Path(temporary_directory)
        remote_output = temporary / "modal"
        try:
            modal.run(job, remote_output)
        except KeyboardInterrupt as error:
            modal.cancel()
            raise OcrError("OCR cancelled") from error
        artifacts = temporary / "artifacts"
        result = extract_ocr_output(remote_output, artifacts, job=job)
        document = {
            "arxiv_id": document_id,
            "oai_datestamp": paper["oai_datestamp"],
            "source_record_sha256": paper["source_record_sha256"],
            "pdf_url": paper["pdf_url"],
            "pdf_sha256": result.pdf_sha256,
            "pdf_size_bytes": result.pdf_size_bytes,
            "page_count": result.page_count,
            "processing_id": result.processing_id,
            "model_repository": config.model.repository,
            "model_revision": config.model.revision,
            "layout_model_repository": config.layout_model.repository,
            "layout_model_revision": config.layout_model.revision,
            "sdk_version": config.sdk_version,
            "config_hash": config.configuration_hash,
            "artifact_uri": store.artifact_uri(document_id, result.processing_id),
            "manifest_sha256": result.manifest_sha256,
            "processed_at": datetime.now(UTC),
        }
        store.publish(document, extracted=artifacts, result=result)
    return {
        "arxiv_id": document_id,
        "artifact_uri": document["artifact_uri"],
        "processing_id": document["processing_id"],
    }


def run_arxiv_ocr(arxiv_id: str) -> dict[str, object]:
    """Compose AWS and Modal clients for one local CLI invocation."""
    document_id = arxiv_id.strip()
    if not document_id:
        raise ValueError("arxiv_id must not be empty")
    settings = get_settings()
    config = load_config()
    credentials = _modal_credentials()
    token_id = credentials.get("token_id")
    token_secret = credentials.get("token_secret")
    if not isinstance(token_id, str) or not token_id:
        raise RuntimeError("Modal secret must contain a non-empty token_id")
    if not isinstance(token_secret, str) or not token_secret:
        raise RuntimeError("Modal secret must contain a non-empty token_secret")
    catalog = load_iceberg_catalog(region_name=settings.aws_region)
    product = load_contracts(settings.contracts_dir).curated_product("arxiv")

    def table(key: str) -> Table:
        return catalog.load_table(product.table_identifier(key).iceberg)

    return process_arxiv_document(
        document_id,
        store=ArxivOcrStore(
            papers=table("papers"),
            documents=table("ocr_documents"),
            curated_uri=get_runtime_parameter(settings.environment, "storage/curated_uri"),
            product_name=product.name,
            s3_client=boto3.client("s3"),
        ),
        config=config,
        modal=ModalOcr(
            token_id=token_id,
            token_secret=token_secret,
            config=config,
        ),
    )
