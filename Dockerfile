FROM ghcr.io/astral-sh/uv:0.11.30 AS uv

FROM python:3.13.14-slim AS base

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock .python-version ./

FROM base AS project-wheel
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --out-dir /dist

FROM base AS runtime-dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM runtime-dependencies AS runtime
COPY --from=project-wheel /dist /dist
COPY contracts ./contracts
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv/bin/python --no-deps /dist/*.whl

CMD ["lakehouse", "--help"]

FROM base AS orchestration-dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra orchestration --no-install-project

FROM orchestration-dependencies AS orchestration
COPY --from=project-wheel /dist /dist
COPY contracts ./contracts
COPY dbt ./dbt
COPY orchestration ./orchestration
COPY runners ./runners
COPY prefect.yaml ./prefect.yaml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv/bin/python --no-deps /dist/*.whl

CMD ["prefect", "--help"]
