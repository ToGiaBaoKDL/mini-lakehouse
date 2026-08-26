# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.11.30@sha256:93b61e21202b1dab861092748e46bbd6e0e41dd84f59b9174efd2353186e1b47 AS uv

FROM python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ARG TARGETARCH
ENV HOME=/tmp \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/arxiv-lens/src \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock .python-version ./
COPY arxiv-lens/pyproject.toml ./arxiv-lens/pyproject.toml
COPY arxiv-lens/src ./arxiv-lens/src
COPY arxiv-lens/.streamlit ./.streamlit
COPY lakehouse/catalog/pyproject.toml ./lakehouse/catalog/pyproject.toml
COPY lakehouse/catalog/src ./lakehouse/catalog/src
COPY ocr-engine/pyproject.toml ./ocr-engine/pyproject.toml
COPY ocr-engine/src ./ocr-engine/src
COPY lakehouse/contracts ./lakehouse/contracts
COPY infra/runtime/identity/install-aws-signing-helper /tmp/install-aws-signing-helper

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package arxiv-lens \
    && groupadd --gid 10001 lens \
    && useradd --uid 10001 --gid lens --create-home --shell /usr/sbin/nologin lens \
    && /tmp/install-aws-signing-helper "$TARGETARCH" \
    && rm -f /usr/local/bin/uv /usr/local/bin/uvx /tmp/install-aws-signing-helper

USER lens
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8501/_stcore/health', timeout=2)"
CMD ["opentelemetry-instrument", "streamlit", "run", "arxiv-lens/src/arxiv_lens/app.py"]
