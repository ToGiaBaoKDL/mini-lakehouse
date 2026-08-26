"""Read-only Streamlit application for inspecting ArXiv OCR outputs."""

import streamlit as st
from document_ocr.protocol import OcrDocumentManifest, OcrElement, OcrPageMarkdownBundle
from lakehouse.aws import get_runtime_parameter
from lakehouse.config.settings import get_settings
from lakehouse.logging import configure_logging
from loguru import logger

from arxiv_lens import state
from arxiv_lens.artifacts import ArtifactRepository, OcrArtifactContent
from arxiv_lens.components import (
    document_label,
    render_document_header,
    render_document_markdown,
    render_elements,
    render_hero,
)
from arxiv_lens.models import OcrDocument, OcrDocumentFilter, OcrDocumentSummary
from arxiv_lens.repository import ArxivRepository
from arxiv_lens.theme import apply_theme

SETTINGS = get_settings()
configure_logging(
    SETTINGS.log_level,
    json_logs=SETTINGS.environment == "production",
)
LOGGER = logger.bind(component="arxiv-lens")
st.set_page_config(
    page_title="ArXiv Lens",
    page_icon=":material/document_scanner:",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_theme()
state.initialize(st.session_state)


@st.cache_resource(show_spinner=False)
def arxiv_repository() -> ArxivRepository:
    return ArxivRepository(SETTINGS)


@st.cache_resource(show_spinner=False)
def artifact_repository() -> ArtifactRepository:
    return ArtifactRepository(
        curated_uri=get_runtime_parameter(SETTINGS.environment, "storage/curated_uri"),
        region_name=SETTINGS.aws_region,
    )


@st.cache_data(max_entries=64, show_spinner=False)
def load_documents(filters: OcrDocumentFilter) -> tuple[OcrDocumentSummary, ...]:
    return arxiv_repository().documents(filters)


@st.cache_data(max_entries=128, show_spinner=False)
def load_document(arxiv_id: str) -> OcrDocument | None:
    return arxiv_repository().document(arxiv_id)


@st.cache_data(ttl=300, max_entries=64, show_spinner=False)
def load_manifest(document: OcrDocument) -> OcrDocumentManifest:
    return artifact_repository().manifest(document)


@st.cache_data(ttl=300, max_entries=24, show_spinner=False)
def load_page_image(
    document: OcrDocument,
    manifest: OcrDocumentManifest,
    page_number: int,
) -> OcrArtifactContent:
    return artifact_repository().page_image(document, manifest, page_number=page_number)


@st.cache_data(ttl=300, max_entries=12, show_spinner=False)
def load_page_markdowns(
    document: OcrDocument,
    manifest: OcrDocumentManifest,
) -> OcrPageMarkdownBundle:
    return artifact_repository().page_markdowns(document, manifest)


@st.cache_data(ttl=300, max_entries=8, show_spinner=False)
def load_elements(
    document: OcrDocument,
    manifest: OcrDocumentManifest,
) -> tuple[OcrElement, ...]:
    return artifact_repository().elements(document, manifest)


def on_document_change() -> None:
    state.reset_page(st.session_state)


def refresh_document_data() -> None:
    """Refresh mutable Iceberg state while retaining immutable artifact caches."""
    load_documents.clear()
    load_document.clear()


def render_sidebar() -> OcrDocumentFilter:
    with st.sidebar:
        st.markdown("### ArXiv Lens")
        st.caption("Read-only inspection of curated ArXiv outputs")
        if st.button(
            "Refresh data",
            icon=":material/refresh:",
            width="stretch",
            type="secondary",
        ):
            refresh_document_data()
        st.divider()
        with st.form("document-filters", border=False):
            st.text_input(
                "Search",
                key=state.SessionKey.SEARCH,
                placeholder="ArXiv ID or paper title",
                icon=":material/search:",
            )
            st.slider(
                "Result limit",
                min_value=10,
                max_value=200,
                step=10,
                key=state.SessionKey.RESULT_LIMIT,
            )
            st.form_submit_button(
                "Apply filters",
                icon=":material/filter_alt:",
                width="stretch",
                type="primary",
            )
        return OcrDocumentFilter.model_validate(
            {
                "search": st.session_state[state.SessionKey.SEARCH],
                "limit": st.session_state[state.SessionKey.RESULT_LIMIT],
            }
        )


def select_document(
    documents: tuple[OcrDocumentSummary, ...],
) -> OcrDocumentSummary | None:
    document_by_id = {document.arxiv_id: document for document in documents}
    selected_id = state.reconcile_document(st.session_state, tuple(document_by_id))
    if selected_id is None:
        return None
    with st.sidebar:
        st.selectbox(
            "Paper",
            tuple(document_by_id),
            format_func=lambda arxiv_id: document_label(document_by_id[arxiv_id]),
            key=state.SessionKey.DOCUMENT_ID,
            on_change=on_document_change,
        )
    return document_by_id[str(st.session_state[state.SessionKey.DOCUMENT_ID])]


def select_page(page_count: int) -> int:
    state.clamp_page(st.session_state, page_count)
    selected = st.number_input(
        "Page",
        min_value=1,
        max_value=page_count,
        step=1,
        key=state.SessionKey.PAGE_NUMBER,
    )
    return int(selected)


@st.fragment
def render_artifacts(document: OcrDocument) -> None:
    try:
        manifest = load_manifest(document)
    except Exception:
        LOGGER.exception("Cannot load OCR manifest for {}", document.arxiv_id)
        st.error("Cannot validate this OCR artifact manifest. Check the application logs.")
        return

    _, selector_column = st.columns((2.2, 1), gap="large")
    with selector_column:
        page_number = select_page(document.page_count)

    try:
        image = load_page_image(document, manifest, page_number)
        markdown = load_page_markdowns(document, manifest).markdown(page_number)
        elements = tuple(
            element
            for element in load_elements(document, manifest)
            if element.page_number == page_number
        )

        page_column, markdown_column = st.columns(2, gap="large")
        with page_column:
            st.markdown("#### Annotated page")
            st.image(
                image.data,
                caption=f"Page {page_number} of {document.page_count}",
                width="stretch",
            )
        with markdown_column:
            st.markdown("#### Markdown")
            with st.container(border=True, key="ocr-markdown-panel"):
                render_document_markdown(markdown)

        st.markdown("#### Elements")
        render_elements(elements)
    except Exception:
        LOGGER.exception(
            "Cannot render OCR artifact for {} page {}",
            document.arxiv_id,
            page_number,
        )
        st.error("Cannot load the selected OCR artifact. Check the application logs.")


def main() -> None:
    render_hero()
    filters = render_sidebar()
    try:
        documents = load_documents(filters)
    except Exception:
        LOGGER.exception("Cannot list OCR documents")
        st.error("Cannot query OCR documents. Check the application logs.")
        st.stop()

    with st.sidebar:
        st.caption(f"{len(documents)} documents loaded")
    document = select_document(documents)
    if document is None:
        st.info("No OCR documents match the current filters.")
        return

    try:
        detail = load_document(document.arxiv_id)
    except Exception:
        LOGGER.exception("Cannot load OCR document {}", document.arxiv_id)
        st.error("Cannot query the OCR document. Check the application logs.")
        return
    if detail is None:
        st.info("This OCR document no longer exists.")
        return

    render_document_header(detail)
    if detail.abstract:
        with st.expander("Paper abstract"):
            st.write(detail.abstract)
    render_artifacts(detail)


main()
