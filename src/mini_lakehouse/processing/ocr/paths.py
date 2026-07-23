"""Canonical local and object-store paths for OCR results."""

from pathlib import PurePosixPath
from urllib.parse import quote

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts.processors import ProcessorContract


def encoded_arxiv_id(arxiv_id: str) -> str:
    return quote(arxiv_id, safe="")


def runner_document_path(arxiv_id: str, request_id: str) -> PurePosixPath:
    return PurePosixPath("documents", encoded_arxiv_id(arxiv_id), request_id)


def artifact_root_uri(
    settings: Settings,
    processor: ProcessorContract,
    *,
    arxiv_id: str,
    processing_id: str,
) -> str:
    return (
        f"{settings.storage.curated_uri.rstrip('/')}/{processor.artifact_prefix}/"
        f"{encoded_arxiv_id(arxiv_id)}/{processing_id}"
    )
