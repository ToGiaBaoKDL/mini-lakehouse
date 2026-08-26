"""Read curated ArXiv metadata and publish successful OCR documents."""

from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlparse

import pyarrow as pa
from botocore.exceptions import ClientError
from pyiceberg.expressions import EqualTo, Reference
from pyiceberg.expressions.literals import literal
from pyiceberg.table import Table

from document_ocr.identity import file_sha256
from document_ocr.protocol import OcrOutput


class ArxivOcrStore:
    def __init__(
        self,
        *,
        papers: Table,
        documents: Table,
        curated_uri: str,
        product_name: str,
        s3_client: Any,
    ) -> None:
        self._papers = papers
        self._documents = documents
        self._curated_uri = curated_uri.rstrip("/")
        self._product_name = product_name
        self._s3 = s3_client

    @staticmethod
    def _scan(table: Table, field: str, value: str) -> list[dict[str, Any]]:
        rows = (
            table.scan(
                row_filter=EqualTo(
                    term=Reference(field),
                    value=literal(value),
                )
            )
            .to_arrow()
            .to_pylist()
        )
        return cast(list[dict[str, Any]], rows)

    def paper(self, arxiv_id: str) -> dict[str, Any]:
        rows = self._scan(self._papers, "arxiv_id", arxiv_id)
        if len(rows) != 1:
            raise ValueError(
                f"Expected one curated ArXiv paper for {arxiv_id!r}, found {len(rows)}"
            )
        return rows[0]

    def document(self, arxiv_id: str) -> dict[str, Any] | None:
        rows = self._scan(self._documents, "arxiv_id", arxiv_id)
        if len(rows) > 1:
            raise RuntimeError(f"Expected at most one OCR document for {arxiv_id!r}")
        return rows[0] if rows else None

    @staticmethod
    def _s3_location(uri: str) -> tuple[str, str]:
        parsed = urlparse(uri)
        if parsed.scheme != "s3" or not parsed.netloc:
            raise ValueError(f"Expected an S3 URI, received {uri!r}")
        return parsed.netloc, parsed.path.lstrip("/")

    def _publish_file(self, source: Path, uri: str) -> None:
        bucket, key = self._s3_location(uri)
        checksum = file_sha256(source)
        try:
            existing = self._s3.head_object(Bucket=bucket, Key=key)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchKey", "NotFound"}:
                raise
        else:
            metadata = existing.get("Metadata", {})
            if (
                metadata.get("sha256") == checksum
                and existing.get("ContentLength") == source.stat().st_size
            ):
                return
            raise RuntimeError(f"Immutable OCR artifact collision at {uri}")
        self._s3.upload_file(
            str(source),
            bucket,
            key,
            ExtraArgs={"Metadata": {"sha256": checksum}},
        )

    def artifact_uri(self, document_id: str, processing_id: str) -> str:
        document = quote(document_id, safe="")
        return f"{self._curated_uri}/{self._product_name}/artifacts/ocr/{document}/{processing_id}"

    def publish(
        self,
        document: dict[str, Any],
        *,
        extracted: Path,
        result: OcrOutput,
    ) -> None:
        if result.state == "succeeded":
            artifact_uri = document["artifact_uri"]
            for source in sorted(path for path in extracted.rglob("*") if path.is_file()):
                relative_path = source.relative_to(extracted).as_posix()
                self._publish_file(source, f"{artifact_uri}/{relative_path}")
        frame = pa.Table.from_pylist([document], schema=self._documents.schema().as_arrow())
        self._documents.upsert(frame)
