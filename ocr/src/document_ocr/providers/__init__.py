"""Remote OCR execution providers."""

from document_ocr.providers.base import (
    OcrProvider,
    OcrProviderName,
    OcrProviderState,
    OcrRunStatus,
    create_ocr_provider,
)

__all__ = [
    "OcrProvider",
    "OcrProviderName",
    "OcrProviderState",
    "OcrRunStatus",
    "create_ocr_provider",
]
