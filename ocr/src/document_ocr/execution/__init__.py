"""Crash-safe execution boundaries for OCR pipelines."""

from document_ocr.execution.core import ExecutionBackend, ExecutionError, LocalExecution
from document_ocr.execution.modal import ModalCredentials, ModalExecution

__all__ = [
    "ExecutionBackend",
    "ExecutionError",
    "LocalExecution",
    "ModalCredentials",
    "ModalExecution",
]
