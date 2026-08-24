"""Provider-neutral document OCR protocol and adapters."""

from document_ocr.config import OcrConfig, load_ocr_config
from document_ocr.providers.base import (
    OcrLogSink,
    OcrProvider,
    OcrProviderError,
    OcrProviderRunFailedError,
    OcrRunNotFoundError,
)

__all__ = [
    "OcrConfig",
    "OcrLogSink",
    "OcrProvider",
    "OcrProviderError",
    "OcrProviderRunFailedError",
    "OcrRunNotFoundError",
    "load_ocr_config",
]
