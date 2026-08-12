"""ArXiv OCR workflow and persistence tests."""

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
from document_ocr.arxiv import ArxivOcrStore, ArxivOcrWorkflow, OcrError
from document_ocr.config import load_ocr_config
from document_ocr.execution import RemoteExecutionBackend
from document_ocr.identity import canonical_json_bytes, processing_id
from document_ocr.protocol import (
    ArtifactFile,
    OcrDocumentManifest,
    OcrDocumentResult,
    OcrElement,
    OcrJob,
    OcrPageMarkdown,
    OcrPageMarkdownBundle,
    OcrRunResult,
)
from document_ocr.providers.base import (
    OcrRunNotFoundError,
)
from lakehouse.catalog.schema import iceberg_schema
from lakehouse.contracts import load_contracts


class _Scan:
    def __init__(self, rows: list[dict[str, Any]], schema: pa.Schema) -> None:
        self._rows = rows
        self._schema = schema

    def to_arrow(self) -> pa.Table:
        return pa.Table.from_pylist(self._rows, schema=self._schema)


class _Schema:
    def __init__(self, schema: pa.Schema) -> None:
        self._schema = schema

    def as_arrow(self) -> pa.Schema:
        return self._schema


class _Table:
    def __init__(self, schema: pa.Schema, rows: list[dict[str, Any]]) -> None:
        self._schema = schema
        self.rows = rows
        self.upserts = 0

    def schema(self) -> Any:
        return _Schema(self._schema)

    def scan(self, *, row_filter: Any) -> _Scan:
        field = row_filter.term.name
        value = row_filter.literal.value
        return _Scan([row for row in self.rows if row.get(field) == value], self._schema)

    def upsert(self, frame: pa.Table) -> None:
        self.upserts += 1
        for incoming in frame.to_pylist():
            self.rows = [row for row in self.rows if row["run_id"] != incoming["run_id"]]
            self.rows.append(incoming)

    def overwrite(self, frame: pa.Table, *, overwrite_filter: Any) -> None:
        field = overwrite_filter.term.name
        value = overwrite_filter.literal.value
        self.rows = [row for row in self.rows if row.get(field) != value]
        self.rows.extend(frame.to_pylist())


class _Catalog:
    def __init__(self, tables: dict[tuple[str, ...], _Table]) -> None:
        self.tables = tables

    def load_table(self, identifier: tuple[str, ...]) -> _Table:
        return self.tables[identifier]


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


class _Provider:
    name = "kaggle"
    reference = "test/arxiv-ocr"

    def __init__(self, processor: Any) -> None:
        self._processor = processor
        self.job: OcrJob | None = None
        self.submissions = 0

    def submit(self, job: OcrJob) -> str:
        self.job = job
        self.submissions += 1
        return "test/arxiv-ocr"

    def wait(self, provider_run_id: str, log: Any) -> None:
        log(f"{provider_run_id}: complete")

    def download_output(self, provider_run_id: str, destination: Path) -> None:
        assert self.job is not None
        request = self.job.document
        pdf_sha256 = "d" * 64
        process = processing_id(
            document_id=request.document_id,
            pdf_sha256=pdf_sha256,
            configuration_hash=self.job.config_hash,
        )
        element = OcrElement(
            element_id="e" * 64,
            page_number=1,
            reading_order=0,
            element_type="text",
            text_content="Test",
            markdown_content="Test",
        )
        elements = gzip.compress(f"{element.model_dump_json()}\n".encode(), mtime=0)
        pages = gzip.compress(
            OcrPageMarkdownBundle(pages=(OcrPageMarkdown(page_number=1, markdown="Test"),))
            .model_dump_json()
            .encode(),
            mtime=0,
        )
        artifacts = {
            "elements.jsonl.gz": elements,
            "pages.json.gz": pages,
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
        document_manifest = canonical_json_bytes(
            OcrDocumentManifest(
                document_id=request.document_id,
                files=files,
                page_count=1,
                pdf_sha256=pdf_sha256,
                pdf_size_bytes=100,
                processing_id=process,
            ).model_dump(mode="json")
        )
        archive = _archive(artifacts)
        document = OcrDocumentManifest.model_validate_json(document_manifest)
        run_result = OcrRunResult(
            run_id=self.job.run_id,
            created_at=datetime.now(UTC),
            archive_sha256=hashlib.sha256(archive).hexdigest(),
            archive_size_bytes=len(archive),
            result=OcrDocumentResult(
                request_id=request.request_id,
                document_id=request.document_id,
                state="succeeded",
                pdf_sha256=pdf_sha256,
                pdf_size_bytes=100,
                page_count=1,
                processing_id=process,
                manifest_sha256=hashlib.sha256(document_manifest).hexdigest(),
            ),
            document=document,
        )
        destination.mkdir()
        (destination / "artifacts.tar.zst").write_bytes(archive)
        (destination / "result.json").write_text(
            run_result.model_dump_json(),
            encoding="utf-8",
        )


class _MissingProvider(_Provider):
    def wait(self, provider_run_id: str, log: Any) -> None:
        raise OcrRunNotFoundError(f"{provider_run_id} is missing")


def _workflow(
    provider_type: type[_Provider] = _Provider,
) -> tuple[
    ArxivOcrWorkflow,
    _Provider,
    dict[tuple[str, ...], _Table],
    Any,
    _S3,
]:
    contracts = load_contracts()
    product = contracts.curated_product("arxiv")
    tables: dict[tuple[str, ...], _Table] = {}
    for table in product.tables:
        rows: list[dict[str, Any]] = []
        if table.key == "papers":
            rows.append(
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
            )
        tables[product.table_identifier(table.key).iceberg] = _Table(
            iceberg_schema(table.columns, table.primary_key).as_arrow(),
            rows,
        )
    processor = load_ocr_config("arxiv_glm_ocr")
    provider = provider_type(processor)
    s3 = _S3()
    catalog = _Catalog(tables)
    store = ArxivOcrStore(
        papers=cast(Any, catalog.load_table(product.table_identifier("papers").iceberg)),
        runs=cast(
            Any,
            catalog.load_table(product.table_identifier("ocr_document_runs").iceberg),
        ),
        elements=cast(
            Any,
            catalog.load_table(product.table_identifier("ocr_document_elements").iceberg),
        ),
        curated_uri="s3://curated",
        product_name=product.name,
        s3_client=s3,
    )
    workflow = ArxivOcrWorkflow(
        store=store,
        processor=processor,
        execution=RemoteExecutionBackend(cast(Any, provider)),
    )
    return workflow, provider, tables, product, s3


def test_arxiv_workflow_runs_and_publishes_exactly_one_document() -> None:
    workflow, provider, tables, product, s3 = _workflow()

    first = workflow.run("2607.00001")
    second = workflow.run("2607.00001")

    assert first["state"] == "imported"
    assert second["run_id"] == first["run_id"]
    assert provider.submissions == 1
    runs = tables[product.table_identifier("ocr_document_runs").iceberg]
    assert len(runs.rows) == 1
    assert runs.upserts == 3
    assert len(tables[product.table_identifier("ocr_document_elements").iceberg].rows) == 1
    keys = {key for _, key in s3.objects}
    assert any(key.endswith("/manifest.json") for key in keys)
    assert any(key.endswith("/pages.json.gz") for key in keys)
    assert not any(key.endswith(".pdf") for key in keys)


def test_arxiv_workflow_fails_a_missing_remote_run() -> None:
    workflow, provider, tables, product, _ = _workflow(_MissingProvider)

    with pytest.raises(OcrError, match="is missing"):
        workflow.run("2607.00001")
    with pytest.raises(OcrError, match="is missing"):
        workflow.run("2607.00001")

    runs = tables[product.table_identifier("ocr_document_runs").iceberg]
    assert len(runs.rows) == 2
    assert provider.submissions == 2
    assert {row["attempt"] for row in runs.rows} == {1, 2}
    assert {row["state"] for row in runs.rows} == {"failed"}
