"""Reusable presentation components without storage or SQL knowledge."""

from html import escape
from html.parser import HTMLParser

import streamlit as st
from document_ocr.protocol import OcrElement

from arxiv_lens.models import OcrDocument, OcrDocumentSummary


class _TableBlockParser(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self._source = source
        self._line_offsets = [0]
        for index, character in enumerate(source):
            if character == "\n":
                self._line_offsets.append(index + 1)
        self._depth = 0
        self._start: int | None = None
        self.ranges: list[tuple[int, int]] = []

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_offsets[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag != "table":
            return
        if self._depth == 0:
            self._start = self._offset()
        self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag != "table" or self._depth == 0:
            return
        self._depth -= 1
        if self._depth == 0 and self._start is not None:
            end = self._source.find(">", self._offset())
            if end >= 0:
                self.ranges.append((self._start, end + 1))
            self._start = None


def render_hero() -> None:
    st.markdown(
        """
        <section class="ocr-hero">
          <div class="ocr-eyebrow">ArXiv · Document Extraction</div>
          <h1>ArXiv Lens</h1>
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


def render_document_header(document: OcrDocument) -> None:
    title = document.title or f"ArXiv {document.arxiv_id}"
    st.markdown(
        f'<h2 class="ocr-paper-title"><a href="{escape(document.paper_url, quote=True)}" '
        f'target="_blank" rel="noopener noreferrer">{escape(title)}</a></h2>',
        unsafe_allow_html=True,
    )
    metadata = [
        f"arXiv:{document.arxiv_id}",
        f"OAI {document.oai_datestamp.isoformat()}",
        f"PDF {_display_bytes(document.pdf_size_bytes)}",
        f"Pages {document.page_count}",
    ]
    st.caption(" · ".join(metadata))


def render_document_markdown(markdown: str) -> None:
    """Render native Markdown while isolating GLM-OCR HTML tables."""
    parser = _TableBlockParser(markdown)
    parser.feed(markdown)
    position = 0
    for start, end in parser.ranges:
        if position < start:
            st.markdown(markdown[position:start])
        st.html(markdown[start:end], unsafe_allow_javascript=False)
        position = end
    if position < len(markdown) or not parser.ranges:
        st.markdown(markdown[position:])


def render_elements(elements: tuple[OcrElement, ...]) -> None:
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


def _display_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"
