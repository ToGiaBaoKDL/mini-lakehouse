import gzip
import hashlib
from collections.abc import Mapping
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from document_ocr.identity import canonical_json_bytes
from document_ocr.protocol import (
    ArtifactFile,
    OcrPageMarkdown,
    OcrPageMarkdownBundle,
)
from lakehouse.config.settings import Settings

from apps.arxiv_inspector import components, state
from apps.arxiv_inspector.data.artifacts import OcrArtifactReader, S3ObjectReader
from apps.arxiv_inspector.data.athena import AthenaReader
from apps.arxiv_inspector.data.models import (
    OcrDocumentFilter,
    OcrDocumentRun,
    OcrRunState,
    OcrStateFilter,
)
from apps.arxiv_inspector.data.repository import ArxivDocumentRepository


class _QueryReader:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.statement = ""
        self.database = ""
        self.parameters: dict[str, Any] = {}

    def query(
        self,
        sql: str,
        *,
        database: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> pd.DataFrame:
        self.statement = sql
        self.database = database
        self.parameters = dict(parameters or {})
        return self.frame


class _ObjectStore:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.reads: list[tuple[str, int]] = []

    def read_bytes(self, uri: str, *, max_bytes: int) -> bytes:
        self.reads.append((uri, max_bytes))
        value = self.objects[uri]
        if len(value) > max_bytes:
            raise ValueError("oversized")
        return value


def test_athena_reader_uses_primary_with_explicit_query_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def read_sql_query(**kwargs: Any) -> pd.DataFrame:
        captured.update(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr(
        "apps.arxiv_inspector.data.athena.wr.athena.read_sql_query",
        read_sql_query,
    )
    reader = AthenaReader(
        workgroup="primary",
        s3_output="s3://query-results/arxiv-inspector",
        region_name="ap-southeast-1",
    )

    reader.query("SELECT 1", database="curated_arxiv")

    assert captured["workgroup"] == "primary"
    assert captured["s3_output"] == "s3://query-results/arxiv-inspector"


def _run(manifest_sha256: str) -> OcrDocumentRun:
    return OcrDocumentRun(
        request_id="1" * 64,
        run_id="2" * 64,
        arxiv_id="2607.20571",
        title="A paper",
        pdf_url="https://arxiv.org/pdf/2607.20571",
        oai_datestamp=date(2026, 7, 25),
        state=OcrRunState.IMPORTED,
        attempt=1,
        processing_id="3" * 64,
        artifact_uri=f"s3://curated/arxiv/ocr/papers/2607.20571/{'3' * 64}",
        manifest_sha256=manifest_sha256,
        pdf_sha256="4" * 64,
        pdf_size_bytes=100,
        page_count=1,
        model_repository="zai-org/GLM-OCR",
        model_revision="5" * 40,
        layout_model_repository="PaddlePaddle/PP-DocLayoutV3_safetensors",
        layout_model_revision="6" * 40,
        adapter_version="1.0.0",
        started_at=datetime(2026, 7, 26, tzinfo=UTC),
        completed_at=datetime(2026, 7, 26, 1, tzinfo=UTC),
    )


def test_run_label_normalizes_athena_timestamp_to_display_timezone() -> None:
    run = _run("9" * 64).model_copy(update={"completed_at": pd.Timestamp("2026-07-26T01:00:00Z")})

    assert components.run_label(run).startswith("26 Jul 2026 · 08:00")


def test_document_repository_keeps_search_and_state_parameterized() -> None:
    frame = pd.DataFrame(
        [
            {
                "arxiv_id": "2607.20571",
                "title": "A paper",
                "state": "imported",
                "attempt": 1,
                "page_count": 3,
                "processing_id": "3" * 64,
                "model_repository": "zai-org/GLM-OCR",
                "model_revision": "5" * 40,
                "completed_at": datetime(2026, 7, 26, tzinfo=UTC),
            }
        ]
    )
    reader = _QueryReader(frame)

    documents = ArxivDocumentRepository(Settings(environment="dev"), reader=reader).documents(
        OcrDocumentFilter(search="Paper%_", state=OcrStateFilter.IMPORTED, limit=20),
    )

    assert documents[0].arxiv_id == "2607.20571"
    assert reader.database == "curated_arxiv"
    assert reader.parameters == {"state": "imported", "search": "paper%_"}
    assert "LIMIT 20" in reader.statement
    assert "Paper%_" not in reader.statement
    assert "strpos" in reader.statement
    assert " LIKE " not in reader.statement
    assert "document.*" not in reader.statement


def test_artifact_reader_uses_verified_manifest_and_declared_page_path() -> None:
    image = b"annotated-page"
    page_markdown = gzip.compress(
        OcrPageMarkdownBundle(
            pages=(OcrPageMarkdown(page_number=1, markdown="# Page one\n"),),
        )
        .model_dump_json()
        .encode(),
        mtime=0,
    )
    files = (
        ArtifactFile(
            relative_path="elements.jsonl.gz",
            sha256="1" * 64,
            size_bytes=1,
            media_type="application/gzip",
        ),
        ArtifactFile(
            relative_path="pages.json.gz",
            sha256=hashlib.sha256(page_markdown).hexdigest(),
            size_bytes=len(page_markdown),
            media_type="text/markdown; charset=utf-8",
        ),
        ArtifactFile(
            relative_path="layout_vis/page-0001.jpg",
            sha256=hashlib.sha256(image).hexdigest(),
            size_bytes=len(image),
            media_type="image/jpeg",
        ),
    )
    manifest_payload = canonical_json_bytes(
        {
            "schema_version": "3.0.0",
            "document_id": "2607.20571",
            "files": [file.model_dump(mode="json") for file in files],
            "page_count": 1,
            "pdf_sha256": "4" * 64,
            "pdf_size_bytes": 100,
            "processing_id": "3" * 64,
        }
    )
    root = f"s3://curated/arxiv/ocr/papers/2607.20571/{'3' * 64}"
    store = _ObjectStore(
        {
            f"{root}/manifest.json": manifest_payload,
            f"{root}/pages.json.gz": page_markdown,
            f"{root}/layout_vis/page-0001.jpg": image,
        }
    )
    run = _run(hashlib.sha256(manifest_payload).hexdigest())
    reader = OcrArtifactReader(store, curated_uri="s3://curated")  # type: ignore[arg-type]

    manifest = reader.manifest(run)
    page = reader.page_image(run, manifest, page_number=1)

    assert page.data == image
    assert page.media_type == "image/jpeg"
    assert reader.page_markdowns(run, manifest).markdown(1) == "# Page one\n"
    with pytest.raises(RuntimeError, match="lineage"):
        reader.manifest(run.model_copy(update={"pdf_size_bytes": 101}))


def test_artifact_reader_rejects_manifest_checksum_drift() -> None:
    root = f"s3://curated/arxiv/ocr/papers/2607.20571/{'3' * 64}"
    reader = OcrArtifactReader(
        _ObjectStore({f"{root}/manifest.json": b"{}"}),  # type: ignore[arg-type]
        curated_uri="s3://curated",
    )

    with pytest.raises(RuntimeError, match="checksum"):
        reader.manifest(_run("9" * 64))


def test_document_run_exposes_the_canonical_arxiv_paper_url() -> None:
    assert _run("9" * 64).paper_url == "https://arxiv.org/abs/2607.20571"
    assert (
        _run("9" * 64).model_copy(update={"arxiv_id": "hep-th/9901001"}).paper_url
        == "https://arxiv.org/abs/hep-th/9901001"
    )


def test_run_header_shows_page_count_only_for_imported_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def ignore_markdown(*args: object, **kwargs: object) -> None:
        pass

    captions: list[str] = []
    monkeypatch.setattr(components.st, "markdown", ignore_markdown)
    monkeypatch.setattr(components.st, "caption", captions.append)

    components.render_run_header(_run("9" * 64))
    assert captions == ["arXiv:2607.20571 · OAI 2026-07-25 · PDF 100.0 B · Pages 1"]

    captions.clear()
    failed = _run("9" * 64).model_copy(update={"state": OcrRunState.FAILED})
    components.render_run_header(failed)
    assert captions == ["arXiv:2607.20571 · OAI 2026-07-25 · PDF 100.0 B"]


def test_document_session_resets_dependent_selection_consistently() -> None:
    session: dict[str, object] = {}
    state.initialize(session)

    assert state.reconcile_document(session, ("paper-a", "paper-b")) == "paper-a"
    session[state.SessionKey.RUN_ID] = "old-run"
    session[state.SessionKey.PAGE_NUMBER] = 7

    assert state.reconcile_document(session, ("paper-b",)) == "paper-b"
    assert session[state.SessionKey.RUN_ID] == ""
    assert session[state.SessionKey.PAGE_NUMBER] == 1
    assert state.reconcile_run(session, ("new-run",)) == "new-run"
    session[state.SessionKey.PAGE_NUMBER] = 99
    assert state.clamp_page(session, 3) == 3


def test_s3_reader_uses_one_bounded_get_request(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"artifact"

    class Client:
        def __init__(self) -> None:
            self.requests: list[dict[str, str]] = []
            self.body = BytesIO(payload)

        def get_object(self, **request: str) -> dict[str, object]:
            self.requests.append(request)
            return {"ContentLength": len(payload), "Body": self.body}

    class Session:
        def __init__(self, client: Client) -> None:
            self._client = client

        def client(self, service_name: str) -> Client:
            assert service_name == "s3"
            return self._client

    client = Client()

    def session_factory(**_: object) -> Session:
        return Session(client)

    monkeypatch.setattr(
        "apps.arxiv_inspector.data.artifacts.boto3.Session",
        session_factory,
    )

    reader = S3ObjectReader(region_name="ap-southeast-1")

    assert reader.read_bytes("s3://curated/path/artifact.bin", max_bytes=100) == payload
    assert client.requests == [{"Bucket": "curated", "Key": "path/artifact.bin"}]
    assert client.body.closed


def test_arxiv_inspector_owns_config_and_bounded_caches() -> None:
    source = Path("apps/arxiv_inspector/app.py").read_text(encoding="utf-8")

    assert Path("apps/arxiv_inspector/.streamlit/config.toml").is_file()
    assert not Path(".streamlit/config.toml").exists()
    assert 'st.form("document-filters"' in source
    assert "max_entries=24" in source
    assert "max_entries=12" in source
    assert "{error}" not in source
