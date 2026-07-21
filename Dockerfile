FROM ghcr.io/astral-sh/uv:latest AS uv

FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock .python-version ./

FROM base AS runtime-dependencies
RUN uv sync --frozen --no-dev --no-install-project

FROM runtime-dependencies AS runtime
COPY README.md ./
COPY src ./src
COPY contracts ./contracts
RUN uv sync --frozen --no-dev --no-editable

CMD ["lakehouse", "--help"]

FROM base AS orchestration-dependencies
RUN uv sync --frozen --no-dev --extra orchestration --no-install-project

FROM orchestration-dependencies AS orchestration
COPY README.md ./
COPY src ./src
COPY contracts ./contracts
COPY dbt ./dbt
COPY orchestration ./orchestration
COPY prefect.yaml ./prefect.yaml
RUN uv sync --frozen --no-dev --extra orchestration --no-editable

CMD ["prefect", "--help"]

FROM base AS dashboard-dependencies
RUN uv sync --frozen --no-dev --extra dashboard --no-install-project

FROM dashboard-dependencies AS dashboard
COPY README.md ./
COPY src ./src
COPY contracts ./contracts
RUN uv sync --frozen --no-dev --extra dashboard --no-editable

CMD ["streamlit", "--help"]
