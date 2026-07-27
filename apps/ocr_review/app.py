"""Read-only Streamlit application for reviewing ArXiv OCR results."""

from __future__ import annotations

import logging

import streamlit as st

from mini_lakehouse.apps.ocr_review import state
from mini_lakehouse.apps.ocr_review.components import (
    document_label,
    render_elements,
    render_hero,
    render_run_header,
    run_label,
)
from mini_lakehouse.apps.ocr_review.theme import apply_theme
from mini_lakehouse.config.settings import get_settings
from mini_lakehouse.curated_products.arxiv.review import (
    ArxivOcrReviewService,
    OcrDocumentFilter,
    OcrDocumentRun,
    OcrDocumentSummary,
    OcrPageElement,
    OcrStateFilter,
    PublishedOcrManifest,
)
from mini_lakehouse.curated_products.arxiv.review.artifacts import OcrArtifactContent
from mini_lakehouse.processing.ocr.core.protocol import OcrPageMarkdownBundle

LOGGER = logging.getLogger(__name__)
STATE_OPTIONS = tuple(item.value for item in OcrStateFilter)

st.set_page_config(
    page_title="OCR Review · Mini Lakehouse",
    page_icon=":material/document_scanner:",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_theme()
state.initialize(st.session_state)


@st.cache_resource(show_spinner=False)
def review_service() -> ArxivOcrReviewService:
    return ArxivOcrReviewService(get_settings())


@st.cache_data(ttl=30, max_entries=64, show_spinner=False)
def load_documents(filters: OcrDocumentFilter) -> tuple[OcrDocumentSummary, ...]:
    return review_service().documents(filters)


@st.cache_data(ttl=30, max_entries=128, show_spinner=False)
def load_runs(arxiv_id: str) -> tuple[OcrDocumentRun, ...]:
    return review_service().document_runs(arxiv_id)


@st.cache_data(ttl=300, max_entries=128, show_spinner=False)
def load_manifest(run: OcrDocumentRun) -> PublishedOcrManifest:
    return review_service().manifest(run)


@st.cache_data(ttl=300, max_entries=256, show_spinner=False)
def load_page_image(
    run: OcrDocumentRun,
    manifest: PublishedOcrManifest,
    page_number: int,
) -> OcrArtifactContent:
    return review_service().page_image(run, manifest, page_number=page_number)


@st.cache_data(ttl=300, max_entries=256, show_spinner=False)
def load_page_markdowns(
    run: OcrDocumentRun,
    manifest: PublishedOcrManifest,
) -> OcrPageMarkdownBundle:
    return review_service().page_markdowns(run, manifest)


@st.cache_data(ttl=300, max_entries=256, show_spinner=False)
def load_page_elements(processing_id: str, page_number: int) -> tuple[OcrPageElement, ...]:
    return review_service().page_elements(
        processing_id=processing_id,
        page_number=page_number,
    )


def on_document_change() -> None:
    state.reset_run(st.session_state)


def on_run_change() -> None:
    state.reset_page(st.session_state)


def refresh_review_data() -> None:
    """Refresh mutable Iceberg state while retaining immutable artifact caches."""
    load_documents.clear()
    load_runs.clear()


def render_sidebar() -> OcrDocumentFilter:
    with st.sidebar:
        st.markdown("### OCR Review")
        st.caption("Read-only inspection of curated ArXiv outputs")
        if st.button(
            "Refresh data",
            icon=":material/refresh:",
            width="stretch",
            type="secondary",
        ):
            refresh_review_data()
        st.divider()
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


def select_run(runs: tuple[OcrDocumentRun, ...]) -> OcrDocumentRun | None:
    run_by_key = {run.run_key: run for run in runs}
    selected_key = state.reconcile_run(st.session_state, tuple(run_by_key))
    if selected_key is None:
        return None
    with st.sidebar:
        st.selectbox(
            "Processing run",
            tuple(run_by_key),
            format_func=lambda run_key: run_label(run_by_key[run_key]),
            key=state.SessionKey.RUN_KEY,
            on_change=on_run_change,
        )
    return run_by_key[str(st.session_state[state.SessionKey.RUN_KEY])]


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


def render_artifacts(run: OcrDocumentRun) -> None:
    assert run.processing_id is not None
    assert run.page_count is not None
    try:
        manifest = load_manifest(run)
    except Exception as error:
        LOGGER.exception("Cannot load OCR manifest for %s", run.arxiv_id)
        st.error(f"Cannot validate this OCR artifact manifest: {error}")
        return

    _, selector_column = st.columns((2.2, 1), gap="large")
    with selector_column:
        page_number = select_page(run.page_count)

    try:
        image = load_page_image(run, manifest, page_number)
        markdown = load_page_markdowns(run, manifest).markdown(page_number)
        elements = load_page_elements(run.processing_id, page_number)

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
                st.markdown(markdown)

        st.markdown("#### Elements")
        render_elements(elements)
    except Exception as error:
        LOGGER.exception(
            "Cannot render OCR artifact for %s page %s",
            run.arxiv_id,
            page_number,
        )
        st.error(f"Cannot load the selected OCR artifact: {error}")


def main() -> None:
    render_hero()
    filters = render_sidebar()
    try:
        documents = load_documents(filters)
    except Exception as error:
        LOGGER.exception("Cannot list OCR documents")
        st.error(f"Cannot query OCR documents: {error}")
        st.stop()

    with st.sidebar:
        st.caption(f"{len(documents)} documents loaded")
    document = select_document(documents)
    if document is None:
        st.info("No OCR documents match the current filters.")
        return

    try:
        runs = load_runs(document.arxiv_id)
    except Exception as error:
        LOGGER.exception("Cannot list OCR runs for %s", document.arxiv_id)
        st.error(f"Cannot query processing history: {error}")
        return
    run = select_run(runs)
    if run is None:
        st.info("This paper does not have a processing run.")
        return

    render_run_header(run)
    if run.abstract:
        with st.expander("Paper abstract"):
            st.write(run.abstract)
    if run.artifacts_available:
        render_artifacts(run)
    else:
        message = run.error_message or "This run has not published OCR artifacts."
        if run.state in {"retryable_failed", "terminal_failed"}:
            st.error(f"{run.error_code or 'ocr_failed'}: {message}")
        else:
            st.info(message)


main()
