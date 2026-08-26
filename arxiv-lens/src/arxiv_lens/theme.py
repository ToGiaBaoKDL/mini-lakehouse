"""ArXiv Lens style layer using Streamlit theme variables."""

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
section[data-testid="stSidebar"] button[data-testid="stBaseButton-primaryFormSubmit"] {
    color: #08110c !important;
}
section[data-testid="stSidebar"] button[data-testid="stBaseButton-primaryFormSubmit"] * {
    color: inherit !important;
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
    background:
        linear-gradient(
            145deg,
            color-mix(in srgb, var(--secondary-background-color) 86%, transparent),
            color-mix(in srgb, var(--background-color) 94%, transparent)
        );
    box-sizing: border-box;
    flex: 1 1 0;
    height: 100%;
    min-height: 0;
    overflow: auto;
    padding: clamp(1rem, 2.4vw, 1.75rem);
}
.st-key-ocr-markdown-panel div[data-testid="stMarkdownContainer"] {
    color: color-mix(in srgb, var(--text-color) 92%, transparent);
    font-size: .96rem;
    line-height: 1.72;
    margin: 0 auto;
    max-width: 52rem;
}
.st-key-ocr-markdown-panel div[data-testid="stMarkdownContainer"] > :first-child {
    margin-top: 0;
}
.st-key-ocr-markdown-panel div[data-testid="stMarkdownContainer"] > :last-child {
    margin-bottom: 0;
}
.st-key-ocr-markdown-panel h1,
.st-key-ocr-markdown-panel h2,
.st-key-ocr-markdown-panel h3,
.st-key-ocr-markdown-panel h4 {
    color: var(--text-color);
    letter-spacing: -.025em;
    line-height: 1.22;
    margin: 1.7em 0 .65em;
}
.st-key-ocr-markdown-panel h1,
.st-key-ocr-markdown-panel h2 {
    border-bottom: 1px solid color-mix(in srgb, var(--primary-color) 22%, transparent);
    padding-bottom: .38em;
}
.st-key-ocr-markdown-panel p,
.st-key-ocr-markdown-panel ul,
.st-key-ocr-markdown-panel ol,
.st-key-ocr-markdown-panel blockquote {
    margin: 0 0 1em;
}
.st-key-ocr-markdown-panel li + li {
    margin-top: .3em;
}
.st-key-ocr-markdown-panel a {
    color: var(--primary-color);
    text-decoration-thickness: .08em;
    text-underline-offset: .16em;
}
.st-key-ocr-markdown-panel blockquote {
    background: color-mix(in srgb, var(--primary-color) 6%, transparent);
    border-left: .22rem solid color-mix(in srgb, var(--primary-color) 72%, transparent);
    border-radius: 0 .55rem .55rem 0;
    color: color-mix(in srgb, var(--text-color) 76%, transparent);
    padding: .75rem 1rem;
}
.st-key-ocr-markdown-panel table {
    border-collapse: separate;
    border-spacing: 0;
    display: block;
    margin: 1.25rem 0;
    max-width: 100%;
    min-width: 100%;
    overflow-x: auto;
    width: max-content;
}
.st-key-ocr-markdown-panel th,
.st-key-ocr-markdown-panel td {
    border-bottom: 1px solid color-mix(in srgb, var(--text-color) 13%, transparent);
    border-right: 1px solid color-mix(in srgb, var(--text-color) 10%, transparent);
    min-width: 7rem;
    padding: .62rem .78rem;
    text-align: left;
    vertical-align: top;
}
.st-key-ocr-markdown-panel th:first-child,
.st-key-ocr-markdown-panel td:first-child {
    border-left: 1px solid color-mix(in srgb, var(--text-color) 10%, transparent);
}
.st-key-ocr-markdown-panel th {
    background: color-mix(in srgb, var(--primary-color) 14%, var(--secondary-background-color));
    border-top: 1px solid color-mix(in srgb, var(--primary-color) 28%, transparent);
    color: var(--text-color);
    font-size: .8rem;
    font-weight: 750;
    letter-spacing: .035em;
    position: sticky;
    text-transform: uppercase;
    top: 0;
    z-index: 1;
}
.st-key-ocr-markdown-panel tbody tr:nth-child(even) td {
    background: color-mix(in srgb, var(--text-color) 3%, transparent);
}
.st-key-ocr-markdown-panel code {
    background: color-mix(in srgb, var(--text-color) 8%, transparent);
    border: 1px solid color-mix(in srgb, var(--text-color) 10%, transparent);
    border-radius: .35rem;
    font-size: .86em;
    padding: .08em .34em;
}
.st-key-ocr-markdown-panel pre {
    border: 1px solid color-mix(in srgb, var(--text-color) 12%, transparent);
    border-radius: .7rem;
    overflow-x: auto;
    padding: 1rem;
}
.st-key-ocr-markdown-panel pre code {
    background: transparent;
    border: 0;
    padding: 0;
}
.st-key-ocr-markdown-panel .katex-display {
    background: color-mix(in srgb, var(--primary-color) 5%, transparent);
    border-radius: .7rem;
    margin: 1.25rem 0;
    overflow-x: auto;
    padding: 1rem .75rem;
}
.st-key-ocr-markdown-panel hr {
    border-color: color-mix(in srgb, var(--primary-color) 24%, transparent);
    margin: 1.75rem 0;
}
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
