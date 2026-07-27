"""Canonical provider-neutral paths for OCR runner results."""

from pathlib import PurePosixPath
from urllib.parse import quote

PAGE_MARKDOWN_BUNDLE_PATH = PurePosixPath("pages.json.gz")


def encoded_arxiv_id(arxiv_id: str) -> str:
    return quote(arxiv_id, safe="")


def runner_document_path(arxiv_id: str, request_id: str) -> PurePosixPath:
    return PurePosixPath("documents", encoded_arxiv_id(arxiv_id), request_id)
