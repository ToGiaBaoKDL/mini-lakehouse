# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.11.30@sha256:93b61e21202b1dab861092748e46bbd6e0e41dd84f59b9174efd2353186e1b47 AS uv

FROM python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock .python-version ./
COPY apps/arxiv_inspector/pyproject.toml ./apps/arxiv_inspector/pyproject.toml
COPY ocr/pyproject.toml ./ocr/pyproject.toml
COPY lakehouse/catalog/pyproject.toml ./lakehouse/catalog/pyproject.toml

FROM base AS catalog-wheel
COPY lakehouse/catalog/src ./lakehouse/catalog/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --package lakehouse --out-dir /dist

FROM base AS ocr-wheel
COPY ocr/src ./ocr/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --package document-ocr --out-dir /dist

FROM base AS dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package arxiv-inspector --no-install-workspace

FROM dependencies
ARG TARGETARCH
ENV HOME=/tmp
RUN groupadd --gid 10001 inspector \
    && useradd --uid 10001 --gid inspector --create-home --shell /usr/sbin/nologin inspector
COPY --from=catalog-wheel /dist /dist
COPY --from=ocr-wheel /dist /dist
COPY infra/runtime/identity/install-aws-signing-helper /tmp/install-aws-signing-helper
COPY --chown=inspector:inspector lakehouse/contracts ./lakehouse/contracts
COPY --chown=inspector:inspector apps/arxiv_inspector ./apps/arxiv_inspector
COPY --chown=inspector:inspector apps/arxiv_inspector/.streamlit ./.streamlit
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv/bin/python --no-deps \
        /dist/lakehouse-*.whl /dist/document_ocr-*.whl \
    && /tmp/install-aws-signing-helper "$TARGETARCH" \
    && rm -rf /dist \
    && rm -f /bin/uv /bin/uvx /tmp/install-aws-signing-helper

USER inspector
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8501/_stcore/health', timeout=2)"
CMD ["opentelemetry-instrument", "streamlit", "run", "apps/arxiv_inspector/app.py"]
