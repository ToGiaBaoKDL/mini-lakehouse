"""Read-only Streamlit application for inspecting ArXiv OCR outputs."""

import streamlit as st
from document_ocr.protocol import (
    OcrDocumentManifest,
    OcrElement,
    OcrPageMarkdownBundle,
)
from lakehouse.config.settings import get_settings
from lakehouse.logging import configure_logging
from loguru import logger

from apps.arxiv_inspector import state
from apps.arxiv_inspector.components import (
    document_label,
    render_document_markdown,
    render_elements,
    render_hero,
    render_run_header,
)
from apps.arxiv_inspector.data import (
    ArxivDocumentService,
    OcrDocumentFilter,
    OcrDocumentRun,
    OcrDocumentSummary,
    OcrRunState,
    OcrStateFilter,
)
from apps.arxiv_inspector.data.artifacts import OcrArtifactContent
from apps.arxiv_inspector.theme import apply_theme

SETTINGS = get_settings()
configure_logging(
    SETTINGS.log_level,
    json_logs=SETTINGS.environment == "production",
)
LOGGER = logger.bind(component="arxiv-inspector")
STATE_OPTIONS = tuple(item.value for item in OcrStateFilter)

st.set_page_config(
    page_title="ArXiv Inspector",
    page_icon=":material/document_scanner:",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_theme()
state.initialize(st.session_state)


@st.cache_resource(show_spinner=False)
def document_service() -> ArxivDocumentService:
    return ArxivDocumentService(SETTINGS)


@st.cache_data(max_entries=64, show_spinner=False)
def load_documents(filters: OcrDocumentFilter) -> tuple[OcrDocumentSummary, ...]:
    return document_service().documents(filters)


@st.cache_data(max_entries=128, show_spinner=False)
def load_run(arxiv_id: str) -> OcrDocumentRun | None:
    return document_service().document_run(arxiv_id)


@st.cache_data(ttl=300, max_entries=64, show_spinner=False)
def load_manifest(run: OcrDocumentRun) -> OcrDocumentManifest:
    return document_service().manifest(run)


@st.cache_data(ttl=300, max_entries=24, show_spinner=False)
def load_page_image(
    run: OcrDocumentRun,
    manifest: OcrDocumentManifest,
    page_number: int,
) -> OcrArtifactContent:
    return document_service().page_image(run, manifest, page_number=page_number)


@st.cache_data(ttl=300, max_entries=12, show_spinner=False)
def load_page_markdowns(
    run: OcrDocumentRun,
    manifest: OcrDocumentManifest,
) -> OcrPageMarkdownBundle:
    return document_service().page_markdowns(run, manifest)


@st.cache_data(ttl=300, max_entries=8, show_spinner=False)
def load_elements(
    run: OcrDocumentRun,
    manifest: OcrDocumentManifest,
) -> tuple[OcrElement, ...]:
    return document_service().elements(run, manifest)


def on_document_change() -> None:
    state.reset_page(st.session_state)


def refresh_document_data() -> None:
    """Refresh mutable Iceberg state while retaining immutable artifact caches."""
    load_documents.clear()
    load_run.clear()


def render_sidebar() -> OcrDocumentFilter:
    with st.sidebar:
        st.markdown("### ArXiv Inspector")
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
            st.selectbox(
                "Latest run state",
                STATE_OPTIONS,
                format_func=lambda value: value.replace("_", " ").title(),
                key=state.SessionKey.STATE_FILTER,
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
                "state": st.session_state[state.SessionKey.STATE_FILTER],
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
def render_artifacts(run: OcrDocumentRun) -> None:
    assert run.processing_id is not None
    assert run.page_count is not None
    try:
        manifest = load_manifest(run)
    except Exception:
        LOGGER.exception("Cannot load OCR manifest for {}", run.arxiv_id)
        st.error("Cannot validate this OCR artifact manifest. Check the application logs.")
        return

    _, selector_column = st.columns((2.2, 1), gap="large")
    with selector_column:
        page_number = select_page(run.page_count)

    try:
        image = load_page_image(run, manifest, page_number)
        markdown = load_page_markdowns(run, manifest).markdown(page_number)
        elements = tuple(
            element
            for element in load_elements(run, manifest)
            if element.page_number == page_number
        )

        page_column, markdown_column = st.columns(2, gap="large")
        with page_column:
            st.markdown("#### Annotated page")
            st.image(
                image.data,
                caption=f"Page {page_number} of {run.page_count}",
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
            run.arxiv_id,
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
        run = load_run(document.arxiv_id)
    except Exception:
        LOGGER.exception("Cannot load the latest OCR run for {}", document.arxiv_id)
        st.error("Cannot query the latest OCR run. Check the application logs.")
        return
    if run is None:
        st.info("This paper does not have an OCR run.")
        return

    render_run_header(run)
    if run.abstract:
        with st.expander("Paper abstract"):
            st.write(run.abstract)
    if run.artifacts_available:
        render_artifacts(run)
    else:
        if run.state == OcrRunState.FAILED:
            st.error("Extraction failed. Check the Airflow task log for the backend traceback.")
        else:
            st.info("This run has not published OCR artifacts.")


main()
