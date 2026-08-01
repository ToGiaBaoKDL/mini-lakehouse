"""ArXiv Inspector style layer using Streamlit theme variables."""

import streamlit as st

_CSS = """
<style>
.ocr-hero {
    border-bottom: 1px solid color-mix(in srgb, var(--primary-color) 35%, transparent);
    margin-bottom: 1.5rem;
    padding: .6rem 0 1.5rem;
}
.ocr-eyebrow {
    color: var(--primary-color);
    font-size: .72rem;
    font-weight: 750;
    letter-spacing: .14em;
    margin-bottom: .55rem;
    text-transform: uppercase;
}
.ocr-hero h1 {
    font-size: clamp(1.75rem, 4vw, 2.6rem);
    letter-spacing: -.045em;
    line-height: 1.05;
    margin: 0;
    max-width: 56rem;
}
.ocr-hero p {
    color: color-mix(in srgb, var(--text-color) 68%, transparent);
    margin: .75rem 0 0;
    max-width: 48rem;
}
div[data-testid="stMarkdownContainer"] h2.ocr-paper-title {
    font-size: clamp(1.05rem, 1.7vw, 1.3rem) !important;
    letter-spacing: -.025em;
    line-height: 1.25;
    margin: 0 0 .65rem;
}
div[data-testid="stMarkdownContainer"] h2.ocr-paper-title > a {
    color: var(--primary-color) !important;
    text-decoration-color: color-mix(in srgb, var(--primary-color) 55%, transparent);
    text-decoration-thickness: .08em;
    text-underline-offset: .16em;
}
div[data-testid="stMarkdownContainer"] h2.ocr-paper-title > a:hover {
    color: var(--primary-color) !important;
    text-decoration-color: var(--primary-color);
}
div[data-testid="stHorizontalBlock"]:has(.st-key-ocr-markdown-panel) {
    align-items: stretch;
}
div[data-testid="stColumn"]:has(.st-key-ocr-markdown-panel) {
    min-height: 0;
}
div[data-testid="stColumn"]:has(.st-key-ocr-markdown-panel)
    > div[data-testid="stVerticalBlock"] {
    height: 100%;
    min-height: 0;
}
div[data-testid="stLayoutWrapper"]:has(> .st-key-ocr-markdown-panel) {
    flex: 1 1 0;
    height: 0;
    min-height: 0;
    overflow: hidden;
}
.st-key-ocr-markdown-panel {
    flex: 1 1 0;
    height: 100%;
    min-height: 0;
    overflow: auto;
}
.ocr-status {
    background: color-mix(in srgb, currentColor 10%, transparent);
    border: 1px solid color-mix(in srgb, currentColor 24%, transparent);
    border-radius: 999px;
    display: inline-block;
    font-size: .75rem;
    font-weight: 700;
    letter-spacing: .025em;
    padding: .28rem .65rem;
}
.ocr-status--imported { color: var(--primary-color); }
.ocr-status--processing { color: #59d5ff; }
.ocr-status--failed { color: #ff7385; }
.ocr-muted {
    color: color-mix(in srgb, var(--text-color) 58%, transparent);
    font-size: .82rem;
}
.ocr-mono {
    font-family: var(--font-monospace);
    font-size: .78rem;
    overflow-wrap: anywhere;
}
</style>
"""


def apply_theme() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
