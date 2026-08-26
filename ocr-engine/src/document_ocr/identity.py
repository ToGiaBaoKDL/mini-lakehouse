"""Deterministic OCR identities derived only from document inputs."""

import hashlib
import json
from pathlib import Path
from typing import Any


def file_sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def job_id(
    *,
    document_id: str,
    source_record_sha256: str,
    configuration_hash: str,
) -> str:
    return canonical_json_sha256(
        {
            "document_id": document_id,
            "config_hash": configuration_hash,
            "source_record_sha256": source_record_sha256,
        }
    )


def processing_id(
    *,
    document_id: str,
    pdf_sha256: str,
    configuration_hash: str,
) -> str:
    return canonical_json_sha256(
        {
            "document_id": document_id,
            "config_hash": configuration_hash,
            "pdf_sha256": pdf_sha256,
        }
    )
