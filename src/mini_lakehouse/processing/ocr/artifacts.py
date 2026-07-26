"""Lakehouse object-store paths for canonical OCR artifacts."""

from mini_lakehouse.config.settings import Settings
from mini_lakehouse.contracts.processors import ProcessorContract
from mini_lakehouse.processing.ocr.core.paths import encoded_arxiv_id


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
