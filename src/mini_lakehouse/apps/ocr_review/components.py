"""Reusable presentation components without storage or SQL knowledge."""

from datetime import datetime
from html import escape

import streamlit as st

from mini_lakehouse.curated_products.arxiv.review import (
    OcrDocumentRun,
    OcrDocumentSummary,
    OcrPageElement,
    OcrRunState,
)


def render_hero() -> None:
    st.markdown(
        """
        <section class="ocr-hero">
          <div class="ocr-eyebrow">ArXiv · GLM-OCR</div>
          <h1>OCR Review</h1>
          <p>Inspect immutable OCR outputs, annotated pages, and extracted content.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )


def document_label(document: OcrDocumentSummary) -> str:
    title = " ".join((document.title or "Untitled paper").split())
    if len(title) > 72:
        title = f"{title[:69]}…"
    return f"{document.arxiv_id} · {title}"


def run_label(run: OcrDocumentRun) -> str:
    timestamp = _display_datetime(run.completed_at or run.prepared_at)
    revision = run.model_revision[:8]
    return f"{timestamp} · {run.state.replace('_', ' ')} · {revision} · attempt {run.attempt_count}"


def render_run_header(run: OcrDocumentRun) -> None:
    title = run.title or f"ArXiv {run.arxiv_id}"
    st.markdown(
        f'<h2 class="ocr-paper-title"><a href="{escape(run.paper_url, quote=True)}" '
        f'target="_blank" rel="noopener noreferrer">{escape(title)}</a></h2>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<span class="ocr-status ocr-status--{escape(run.state)}">'
        f"{escape(run.state.replace('_', ' ').upper())}</span>",
        unsafe_allow_html=True,
    )
    metadata = [
        f"arXiv:{run.arxiv_id}",
        f"OAI {run.oai_datestamp.isoformat()}",
        f"PDF {_display_bytes(run.pdf_size_bytes)}",
    ]
    if run.state == OcrRunState.IMPORTED and run.page_count is not None:
        metadata.append(f"Pages {run.page_count}")
    st.caption(" · ".join(metadata))


def render_elements(elements: tuple[OcrPageElement, ...]) -> None:
    if not elements:
        st.info("No canonical elements were published for this page.")
        return
    st.dataframe(
        [
            {
                "Order": element.reading_order,
                "Type": element.element_type,
                "Content": element.text_content,
                "Bounding box": element.bbox_json,
            }
            for element in elements
        ],
        hide_index=True,
        width="stretch",
        column_config={
            "Order": st.column_config.NumberColumn(width="small"),
            "Type": st.column_config.TextColumn(width="small"),
            "Content": st.column_config.TextColumn(width="large"),
            "Bounding box": st.column_config.TextColumn(width="medium"),
        },
    )


def _display_datetime(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.astimezone().strftime("%d %b %Y · %H:%M")


def _display_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"
