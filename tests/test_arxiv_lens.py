import gzip
import hashlib
from collections.abc import Mapping
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from arxiv_lens import components, state
from arxiv_lens.artifacts import ArtifactRepository
from arxiv_lens.models import (
    OcrDocument,
    OcrDocumentFilter,
)
from arxiv_lens.repository import ArxivRepository
from document_ocr.identity import canonical_json_bytes
from document_ocr.protocol import (
    ArtifactFile,
    OcrElement,
    OcrPageMarkdown,
    OcrPageMarkdownBundle,
)
from lakehouse.config.settings import Settings


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


class _S3:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.reads: list[tuple[str, int]] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        uri = f"s3://{Bucket}/{Key}"
        value = self.objects[uri]
        self.reads.append((uri, len(value)))
        return {"ContentLength": len(value), "Body": BytesIO(value)}


def test_repository_uses_awswrangler_with_explicit_query_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def read_sql_query(**kwargs: Any) -> pd.DataFrame:
        captured.update(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr(
        "arxiv_lens.repository.wr.athena.read_sql_query",
        read_sql_query,
    )

    def runtime_parameter(_environment: str, _key: str) -> str:
        return "s3://query-results/arxiv-lens"

    monkeypatch.setattr("arxiv_lens.repository.get_runtime_parameter", runtime_parameter)
    repository = ArxivRepository(Settings(environment="dev"))

    repository.documents(OcrDocumentFilter())

    assert captured["workgroup"] == "primary"
    assert captured["s3_output"] == "s3://query-results/arxiv-lens"


def _document(manifest_sha256: str) -> OcrDocument:
    return OcrDocument(
        arxiv_id="2607.20571",
        title="A paper",
        abstract="Abstract",
        pdf_url="https://arxiv.org/pdf/2607.20571",
        oai_datestamp=date(2026, 7, 25),
        source_record_sha256="1" * 64,
        processing_id="3" * 64,
        artifact_uri=f"s3://curated/arxiv/artifacts/ocr/2607.20571/{'3' * 64}",
        manifest_sha256=manifest_sha256,
        pdf_sha256="4" * 64,
        pdf_size_bytes=100,
        page_count=1,
        model_repository="zai-org/GLM-OCR",
        model_revision="5" * 40,
        layout_model_repository="PaddlePaddle/PP-DocLayoutV3_safetensors",
        layout_model_revision="6" * 40,
        sdk_version="0.1.5",
        config_hash="7" * 64,
        processed_at=datetime(2026, 7, 26, tzinfo=UTC),
    )


def test_document_repository_keeps_search_parameterized() -> None:
    frame = pd.DataFrame(
        [
            {
                "arxiv_id": "2607.20571",
                "title": "A paper",
                "page_count": 3,
                "processing_id": "3" * 64,
                "model_repository": "zai-org/GLM-OCR",
                "model_revision": "5" * 40,
                "processed_at": datetime(2026, 7, 26, tzinfo=UTC),
            }
        ]
    )
    reader = _QueryReader(frame)

    documents = ArxivRepository(Settings(environment="dev"), query=reader.query).documents(
        OcrDocumentFilter(search="Paper%_", limit=20),
    )

    assert documents[0].arxiv_id == "2607.20571"
    assert reader.database == "curated_arxiv"
    assert reader.parameters == {"search": "paper%_"}
    assert "LIMIT 20" in reader.statement
    assert "Paper%_" not in reader.statement
    assert "strpos" in reader.statement
    assert " LIKE " not in reader.statement
    assert "document.*" not in reader.statement


def test_document_repository_loads_one_document() -> None:
    reader = _QueryReader(pd.DataFrame([_document("9" * 64).model_dump()]))

    document = ArxivRepository(Settings(environment="dev"), query=reader.query).document(
        "2607.20571"
    )

    assert document is not None
    assert document.processing_id == "3" * 64
    assert "LIMIT 1" in reader.statement


def test_artifact_reader_uses_verified_manifest_and_declared_page_path() -> None:
    image = b"annotated-page"
    element = OcrElement(
        element_id="7" * 64,
        page_number=1,
        reading_order=0,
        element_type="text",
        text_content="Page one",
    )
    elements = gzip.compress((element.model_dump_json() + "\n").encode(), mtime=0)
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
            sha256=hashlib.sha256(elements).hexdigest(),
            size_bytes=len(elements),
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
    root = f"s3://curated/arxiv/artifacts/ocr/2607.20571/{'3' * 64}"
    store = _S3(
        {
            f"{root}/manifest.json": manifest_payload,
            f"{root}/elements.jsonl.gz": elements,
            f"{root}/pages.json.gz": page_markdown,
            f"{root}/layout_vis/page-0001.jpg": image,
        }
    )
    document = _document(hashlib.sha256(manifest_payload).hexdigest())
    reader = ArtifactRepository(
        client=store,
        curated_uri="s3://curated",
        region_name="ap-southeast-1",
    )

    manifest = reader.manifest(document)
    page = reader.page_image(document, manifest, page_number=1)

    assert page.data == image
    assert page.media_type == "image/jpeg"
    assert reader.page_markdowns(document, manifest).markdown(1) == "# Page one\n"
    assert reader.elements(document, manifest) == (element,)
    with pytest.raises(RuntimeError, match="lineage"):
        reader.manifest(document.model_copy(update={"pdf_size_bytes": 101}))


def test_artifact_reader_rejects_manifest_checksum_drift() -> None:
    root = f"s3://curated/arxiv/artifacts/ocr/2607.20571/{'3' * 64}"
    reader = ArtifactRepository(
        client=_S3({f"{root}/manifest.json": b"{}"}),
        curated_uri="s3://curated",
        region_name="ap-southeast-1",
    )

    with pytest.raises(RuntimeError, match="checksum"):
        reader.manifest(_document("9" * 64))


def test_document_exposes_the_canonical_arxiv_paper_url() -> None:
    assert _document("9" * 64).paper_url == "https://arxiv.org/abs/2607.20571"
    assert (
        _document("9" * 64).model_copy(update={"arxiv_id": "hep-th/9901001"}).paper_url
        == "https://arxiv.org/abs/hep-th/9901001"
    )


def test_document_header_shows_page_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def ignore_markdown(*args: object, **kwargs: object) -> None:
        pass

    captions: list[str] = []
    monkeypatch.setattr(components.st, "markdown", ignore_markdown)
    monkeypatch.setattr(components.st, "caption", captions.append)

    components.render_document_header(_document("9" * 64))
    assert captions == ["arXiv:2607.20571 · OAI 2026-07-25 · PDF 100.0 B · Pages 1"]


def test_document_markdown_routes_tables_through_streamlit_sanitizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered: list[tuple[str, bool]] = []
    tables: list[tuple[str, bool]] = []

    def markdown(body: str, *, unsafe_allow_html: bool = False) -> None:
        rendered.append((body, unsafe_allow_html))

    def html(body: str, *, unsafe_allow_javascript: bool = False) -> None:
        tables.append((body, unsafe_allow_javascript))

    monkeypatch.setattr(components.st, "markdown", markdown)
    monkeypatch.setattr(components.st, "html", html)
    components.render_document_markdown(
        "## Result\n\n$x < y$\n\n![](<imgs/imageFile1.png>)\n\n"
        '<table onclick="alert(1)"><tr><th rowspan="2">Header</th>'
        '<td colspan="3"><script>alert(2)</script>Value</td></tr></table>\n\nDone.'
    )

    assert rendered[0] == (
        "## Result\n\n$x < y$\n\n![](<imgs/imageFile1.png>)\n\n",
        False,
    )
    assert tables == [
        (
            '<table onclick="alert(1)"><tr><th rowspan="2">Header</th>'
            '<td colspan="3"><script>alert(2)</script>Value</td></tr></table>',
            False,
        )
    ]
    assert rendered[1] == ("\n\nDone.", False)


def test_document_session_resets_dependent_selection_consistently() -> None:
    session: dict[str, object] = {}
    state.initialize(session)

    assert state.reconcile_document(session, ("paper-a", "paper-b")) == "paper-a"
    session[state.SessionKey.PAGE_NUMBER] = 7

    assert state.reconcile_document(session, ("paper-b",)) == "paper-b"
    assert session[state.SessionKey.PAGE_NUMBER] == 1
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
        "arxiv_lens.artifacts.boto3.Session",
        session_factory,
    )

    reader = ArtifactRepository(
        curated_uri="s3://curated",
        region_name="ap-southeast-1",
    )

    assert (
        reader._read_bytes(  # pyright: ignore[reportPrivateUsage]
            "s3://curated/path/artifact.bin",
            max_bytes=100,
        )
        == payload
    )
    assert client.requests == [{"Bucket": "curated", "Key": "path/artifact.bin"}]
    assert client.body.closed


def test_arxiv_lens_owns_config_and_bounded_caches() -> None:
    source = Path("arxiv-lens/src/arxiv_lens/app.py").read_text(encoding="utf-8")
    theme = Path("arxiv-lens/src/arxiv_lens/theme.py").read_text(encoding="utf-8")

    assert Path("arxiv-lens/.streamlit/config.toml").is_file()
    assert not Path(".streamlit/config.toml").exists()
    assert 'st.form("document-filters"' in source
    assert "max_entries=24" in source
    assert "max_entries=12" in source
    assert "max_entries=8" in source
    assert "@st.cache_data(ttl=30," not in source
    assert "@st.fragment\ndef render_artifacts" in source
    assert "{error}" not in source
    assert "Processing run" not in source
    assert 'button[data-testid="stBaseButton-primaryFormSubmit"]' in theme
    assert "color: #08110c !important" in theme
    assert ".st-key-ocr-markdown-panel table" in theme
    assert "position: sticky" in theme
