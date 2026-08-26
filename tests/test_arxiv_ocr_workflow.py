"""ArXiv OCR publishing tests."""

import gzip
import hashlib
import io
import tarfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pytest
import zstandard
from botocore.exceptions import ClientError
from document_ocr.arxiv.service import process_arxiv_document
from document_ocr.arxiv.store import ArxivOcrStore
from document_ocr.config import load_config
from document_ocr.identity import canonical_json_bytes, processing_id
from document_ocr.protocol import (
    ArtifactFile,
    OcrDocumentManifest,
    OcrElement,
    OcrError,
    OcrJob,
    OcrOutput,
    OcrPageMarkdown,
    OcrPageMarkdownBundle,
)
from lakehouse.catalog.schema import iceberg_schema

from lakehouse.contracts import load_contracts


class _Scan:
    def __init__(self, rows: list[dict[str, Any]], schema: pa.Schema) -> None:
        self._rows = rows
        self._schema = schema

    def to_arrow(self) -> pa.Table:
        return pa.Table.from_pylist(self._rows, schema=self._schema)


class _Table:
    def __init__(self, schema: pa.Schema, rows: list[dict[str, Any]]) -> None:
        self._schema = schema
        self.rows = rows
        self.upserts = 0

    def schema(self) -> Any:
        schema = self._schema

        class _Schema:
            def as_arrow(self) -> pa.Schema:
                return schema

        return _Schema()

    def scan(self, *, row_filter: Any) -> _Scan:
        field = row_filter.term.name
        value = row_filter.literal.value
        return _Scan([row for row in self.rows if row.get(field) == value], self._schema)

    def upsert(self, frame: pa.Table) -> None:
        self.upserts += 1
        incoming = frame.to_pylist()[0]
        self.rows = [row for row in self.rows if row["arxiv_id"] != incoming["arxiv_id"]]
        self.rows.append(incoming)


class _S3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        try:
            payload = self.objects[(Bucket, Key)]
        except KeyError as error:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject") from error
        return {
            "ContentLength": len(payload),
            "Metadata": {"sha256": hashlib.sha256(payload).hexdigest()},
        }

    def upload_file(
        self,
        filename: str,
        bucket: str,
        key: str,
        *,
        ExtraArgs: dict[str, Any],
    ) -> None:
        payload = Path(filename).read_bytes()
        assert ExtraArgs["Metadata"]["sha256"] == hashlib.sha256(payload).hexdigest()
        self.objects[(bucket, key)] = payload


def _archive(files: dict[str, bytes]) -> bytes:
    bundle = io.BytesIO()
    with tarfile.open(fileobj=bundle, mode="w") as archive:
        for name, payload in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return zstandard.ZstdCompressor(write_checksum=True).compress(bundle.getvalue())


class _Modal:
    def __init__(self, *, error: str | None = None) -> None:
        self.calls = 0
        self.error = error

    def run(self, job: OcrJob, destination: Path) -> None:
        self.calls += 1
        if self.error:
            raise OcrError(self.error)
        pdf_sha256 = "d" * 64
        process = processing_id(
            document_id=job.document_id,
            pdf_sha256=pdf_sha256,
            configuration_hash=job.config_hash,
        )
        element = OcrElement(
            element_id="e" * 64,
            page_number=1,
            reading_order=0,
            element_type="text",
            text_content="Test",
            markdown_content="Test",
        )
        artifacts = {
            "elements.jsonl.gz": gzip.compress(f"{element.model_dump_json()}\n".encode(), mtime=0),
            "pages.json.gz": gzip.compress(
                OcrPageMarkdownBundle(pages=(OcrPageMarkdown(page_number=1, markdown="Test"),))
                .model_dump_json()
                .encode(),
                mtime=0,
            ),
            "layout_vis/page-0001.jpg": b"annotated",
        }
        files = tuple(
            ArtifactFile(
                relative_path=name,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
                media_type="application/octet-stream",
            )
            for name, payload in sorted(artifacts.items())
        )
        manifest = OcrDocumentManifest(
            document_id=job.document_id,
            files=files,
            page_count=1,
            pdf_sha256=pdf_sha256,
            pdf_size_bytes=100,
            processing_id=process,
        )
        archive = _archive(artifacts)
        destination.mkdir()
        (destination / "artifacts.tar.zst").write_bytes(archive)
        (destination / "result.json").write_text(
            OcrOutput(
                job_id=job.job_id,
                created_at=datetime.now(UTC),
                archive_sha256=hashlib.sha256(archive).hexdigest(),
                archive_size_bytes=len(archive),
                document_id=job.document_id,
                state="succeeded",
                pdf_sha256=pdf_sha256,
                pdf_size_bytes=100,
                page_count=1,
                processing_id=process,
                manifest_sha256=hashlib.sha256(
                    canonical_json_bytes(manifest.model_dump(mode="json"))
                ).hexdigest(),
                manifest=manifest,
            ).model_dump_json(),
            encoding="utf-8",
        )

    def cancel(self) -> None:
        pass


def _dependencies() -> tuple[ArxivOcrStore, _Table, _S3]:
    product = load_contracts().curated_product("arxiv")
    papers_contract = product.table("papers")
    documents_contract = product.table("ocr_documents")
    papers = _Table(
        iceberg_schema(papers_contract.columns, papers_contract.primary_key).as_arrow(),
        [
            {
                "arxiv_id": "2607.00001",
                "oai_identifier": "oai:arXiv.org:2607.00001",
                "title": "Test",
                "abstract": None,
                "license_uri": None,
                "doi": None,
                "journal_ref": None,
                "comments": None,
                "created_date": date(2026, 7, 1),
                "updated_date": date(2026, 7, 29),
                "oai_datestamp": date(2026, 7, 29),
                "pdf_url": "https://arxiv.org/pdf/2607.00001",
                "is_deleted": False,
                "source_record_sha256": "a" * 64,
                "curated_at": datetime.now(UTC),
            }
        ],
    )
    documents = _Table(
        iceberg_schema(documents_contract.columns, documents_contract.primary_key).as_arrow(),
        [],
    )
    s3 = _S3()
    return (
        ArxivOcrStore(
            papers=cast(Any, papers),
            documents=cast(Any, documents),
            curated_uri="s3://curated",
            product_name=product.name,
            s3_client=s3,
        ),
        documents,
        s3,
    )


def test_arxiv_ocr_publishes_once_and_reuses_current_document() -> None:
    store, documents, s3 = _dependencies()
    modal = _Modal()

    first = process_arxiv_document(
        "2607.00001", store=store, config=load_config(), modal=cast(Any, modal)
    )
    second = process_arxiv_document(
        "2607.00001", store=store, config=load_config(), modal=cast(Any, modal)
    )

    assert second == first
    assert modal.calls == 1
    assert documents.upserts == 1
    assert len(documents.rows) == 1
    keys = {key for _, key in s3.objects}
    assert any(key.endswith("/manifest.json") for key in keys)
    assert any(key.endswith("/pages.json.gz") for key in keys)


def test_arxiv_ocr_failure_does_not_persist_state() -> None:
    store, documents, s3 = _dependencies()

    with pytest.raises(OcrError, match="remote failed"):
        process_arxiv_document(
            "2607.00001",
            store=store,
            config=load_config(),
            modal=cast(Any, _Modal(error="remote failed")),
        )

    assert documents.rows == []
    assert s3.objects == {}
