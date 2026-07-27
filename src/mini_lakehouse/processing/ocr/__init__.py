"""Canonical OCR protocol and provider adapters."""

from mini_lakehouse.processing.ocr.core.identity import (
    batch_id,
    config_hash,
    processing_id,
    request_id,
)
from mini_lakehouse.processing.ocr.core.protocol import (
    OcrBatchManifest,
    OcrDocumentRequest,
    OcrDocumentResult,
    OcrElement,
    OcrJob,
)
from mini_lakehouse.processing.ocr.core.text import serialize_plain_text

__all__ = [
    "OcrBatchManifest",
    "OcrDocumentRequest",
    "OcrDocumentResult",
    "OcrElement",
    "OcrJob",
    "batch_id",
    "config_hash",
    "processing_id",
    "request_id",
    "serialize_plain_text",
]
