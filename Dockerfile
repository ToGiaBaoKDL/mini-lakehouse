ARG AIRFLOW_VERSION=3.3.0

FROM ghcr.io/astral-sh/uv:0.11.30 AS uv

FROM python:3.12.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock .python-version ./
COPY ocr/pyproject.toml ./ocr/pyproject.toml

FROM base AS platform-wheel
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --package lakehouse-platform --out-dir /dist

FROM base AS ocr-wheel
COPY README.md ./
COPY ocr/src ./ocr/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --package document-ocr --out-dir /dist

FROM base AS document-inspector-dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra document-inspector --no-install-project

FROM document-inspector-dependencies AS document-inspector
COPY --from=platform-wheel /dist /dist
COPY --from=ocr-wheel /dist /dist
COPY contracts ./contracts
COPY apps/document_inspector ./apps/document_inspector
COPY apps/document_inspector/.streamlit ./.streamlit
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv/bin/python --no-deps \
    /dist/lakehouse_platform-*.whl /dist/document_ocr-*.whl

CMD ["streamlit", "run", "apps/document_inspector/app.py"]

FROM apache/airflow:${AIRFLOW_VERSION}-python3.12 AS airflow
ARG AIRFLOW_VERSION

ENV PYTHONPATH=/opt/airflow

COPY --from=platform-wheel --chown=airflow:0 /dist /tmp/dist
RUN pip install --no-cache-dir \
    "apache-airflow==${AIRFLOW_VERSION}" \
    "apache-airflow-providers-amazon[aiobotocore]==9.32.0" \
    "apache-airflow-providers-slack==9.10.2" \
    "apache-airflow-providers-smtp==3.0.2" \
    /tmp/dist/lakehouse_platform-*.whl

COPY --chown=airflow:0 orchestration /opt/airflow/orchestration

CMD ["airflow", "--help"]
