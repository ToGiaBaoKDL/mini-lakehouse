"""Deterministic OCR identities derived only from business inputs."""

import hashlib
import json
from collections.abc import Mapping, Sequence
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


def config_hash(
    processor: Any,
) -> str:
    """Hash canonical output semantics, excluding execution and delivery tuning.

    ``adapter_version`` owns changes to parsing/normalization code. The explicit
    inference fields below may alter generated content. Ports, timeouts, memory
    allocation, concurrency, eager execution, and runner packaging affect only
    how that output is produced and therefore must not invalidate existing OCR.
    """
    payload = {
        "adapter": processor.adapter,
        "adapter_version": processor.adapter_version,
        "inference": {
            "dtype": processor.inference.dtype,
            "layout_device": processor.inference.layout_device,
            "max_model_len": processor.inference.max_model_len,
            "speculative_tokens": processor.inference.speculative_tokens,
        },
        "layout_model": processor.layout_model.model_dump(mode="json"),
        "model": processor.model.model_dump(mode="json"),
        "output_schema_version": processor.output_schema_version,
    }
    return canonical_json_sha256(payload)


def request_id(
    *,
    arxiv_id: str,
    source_record_sha256: str,
    configuration_hash: str,
) -> str:
    return canonical_json_sha256(
        {
            "arxiv_id": arxiv_id,
            "config_hash": configuration_hash,
            "source_record_sha256": source_record_sha256,
        }
    )


def processing_id(
    *,
    arxiv_id: str,
    pdf_sha256: str,
    configuration_hash: str,
) -> str:
    return canonical_json_sha256(
        {
            "arxiv_id": arxiv_id,
            "config_hash": configuration_hash,
            "pdf_sha256": pdf_sha256,
        }
    )


def successful_document_manifest_payload(
    *,
    arxiv_id: str,
    pdf_sha256: str,
    pdf_size_bytes: int,
    page_count: int,
    processing_id: str,
    files: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return the content manifest, excluding request/attempt identity."""
    return {
        "arxiv_id": arxiv_id,
        "files": list(files),
        "page_count": page_count,
        "pdf_sha256": pdf_sha256,
        "pdf_size_bytes": pdf_size_bytes,
        "processing_id": processing_id,
    }


def successful_document_manifest_sha256(
    *,
    arxiv_id: str,
    pdf_sha256: str,
    pdf_size_bytes: int,
    page_count: int,
    processing_id: str,
    files: Sequence[Mapping[str, Any]],
) -> str:
    return canonical_json_sha256(
        successful_document_manifest_payload(
            arxiv_id=arxiv_id,
            pdf_sha256=pdf_sha256,
            pdf_size_bytes=pdf_size_bytes,
            page_count=page_count,
            processing_id=processing_id,
            files=files,
        )
    )


def batch_id(
    request_ids: Sequence[str],
    *,
    attempts: Mapping[str, int],
) -> str:
    unique_request_ids = set(request_ids)
    if not request_ids or len(unique_request_ids) != len(request_ids):
        raise ValueError("Batch request IDs must be non-empty and unique")
    if unique_request_ids != set(attempts) or any(attempt < 1 for attempt in attempts.values()):
        raise ValueError("Batch attempts must contain one positive value per request")
    return canonical_json_sha256(
        {
            "attempts": {key: attempts[key] for key in sorted(attempts)},
            "request_ids": sorted(request_ids),
        }
    )
