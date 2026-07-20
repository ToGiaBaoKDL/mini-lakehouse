FROM ghcr.io/astral-sh/uv:latest AS uv

FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md .python-version ./
COPY src ./src
COPY dbt_project ./dbt_project
COPY prefect.yaml ./prefect.yaml

FROM base AS runtime
RUN uv sync --frozen --no-dev

CMD ["lakehouse", "--help"]

FROM runtime AS orchestration
RUN uv sync --frozen --no-dev --extra orchestration

FROM runtime AS dashboard
RUN uv sync --frozen --no-dev --extra dashboard
