"""Own ArXiv OCR state in Iceberg and immutable artifacts in S3."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlparse

import pyarrow as pa
from botocore.exceptions import ClientError
from pyiceberg.expressions import EqualTo, Reference
from pyiceberg.expressions.literals import literal
from pyiceberg.table import Table

from document_ocr.identity import file_sha256
from document_ocr.output import read_elements
from document_ocr.protocol import OcrDocumentResult, OcrJob

IMPORTED_STATE = "imported"


class ArxivOcrStore:
    """Persist OCR execution state and publish one validated document."""

    def __init__(
        self,
        *,
        papers: Table,
        runs: Table,
        elements: Table,
        curated_uri: str,
        product_name: str,
        s3_client: Any,
    ) -> None:
        self._papers = papers
        self._runs = runs
        self._elements = elements
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

    def runs(self, arxiv_id: str) -> list[dict[str, Any]]:
        return self._scan(self._runs, "arxiv_id", arxiv_id)

    def save_run(self, run: dict[str, Any]) -> None:
        run["curated_at"] = datetime.now(UTC)
        frame = pa.Table.from_pylist([run], schema=self._runs.schema().as_arrow())
        self._runs.upsert(frame)

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

    def _artifact_uri(self, document_id: str, processing_id: str) -> str:
        document = quote(document_id, safe="")
        return f"{self._curated_uri}/{self._product_name}/artifacts/ocr/{document}/{processing_id}"

    def publish(
        self,
        *,
        job: OcrJob,
        run: dict[str, Any],
        extracted: Path,
        result: OcrDocumentResult,
    ) -> None:
        processing_id = result.processing_id
        artifact_uri = self._artifact_uri(result.document_id, processing_id)
        if result.state == "reused":
            existing = next(
                (
                    row
                    for row in self._scan(self._runs, "processing_id", processing_id)
                    if row["state"] == IMPORTED_STATE
                    and row["manifest_sha256"] == result.manifest_sha256
                    and row["artifact_uri"] is not None
                ),
                None,
            )
            if existing is None:
                raise RuntimeError("Reused OCR content has no imported artifact lineage")
            artifact_uri = existing["artifact_uri"]
        else:
            for source in sorted(path for path in extracted.rglob("*") if path.is_file()):
                relative_path = source.relative_to(extracted).as_posix()
                self._publish_file(source, f"{artifact_uri}/{relative_path}")
            elements = read_elements(
                extracted / "elements.jsonl.gz",
                max_uncompressed_bytes=job.limits.max_output_bytes,
                expected_page_count=result.page_count,
            )
            now = datetime.now(UTC)
            frame = pa.Table.from_pylist(
                [
                    {
                        "processing_id": processing_id,
                        "element_id": element.element_id,
                        "arxiv_id": result.document_id,
                        "page_number": element.page_number,
                        "reading_order": element.reading_order,
                        "element_type": element.element_type,
                        "bbox_json": element.bbox_json,
                        "text_content": element.text_content,
                        "markdown_content": element.markdown_content,
                        "parent_element_id": element.parent_element_id,
                        "raw_attributes_json": element.raw_attributes_json,
                        "curated_at": now,
                    }
                    for element in elements
                ],
                schema=self._elements.schema().as_arrow(),
            )
            self._elements.overwrite(
                frame,
                overwrite_filter=EqualTo(
                    term=Reference("processing_id"),
                    value=literal(processing_id),
                ),
            )
        run.update(
            pdf_sha256=result.pdf_sha256,
            pdf_size_bytes=result.pdf_size_bytes,
            page_count=result.page_count,
            processing_id=processing_id,
            artifact_uri=artifact_uri,
            manifest_sha256=result.manifest_sha256,
            state=IMPORTED_STATE,
            completed_at=datetime.now(UTC),
        )
        self.save_run(run)
