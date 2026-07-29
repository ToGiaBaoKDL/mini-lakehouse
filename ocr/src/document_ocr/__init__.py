"""Provider-neutral document OCR protocol and adapters."""

from document_ocr.config import OcrConfig, load_ocr_config
from document_ocr.providers.base import (
    OcrProvider,
    OcrProviderName,
    OcrProviderState,
    OcrRunStatus,
    create_ocr_provider,
)

__all__ = [
    "OcrConfig",
    "OcrProvider",
    "OcrProviderName",
    "OcrProviderState",
    "OcrRunStatus",
    "create_ocr_provider",
    "load_ocr_config",
]
